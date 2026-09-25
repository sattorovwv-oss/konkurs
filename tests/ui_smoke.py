"""Optional real Chromium layout check using an isolated temporary database.
Run: python tests/ui_smoke.py (after playwright install chromium).
CHROMIUM_EXECUTABLE and UI_ARTIFACT_DIR are optional.
"""

import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from conftest import make_contest, authenticated_client, zip_bytes, png_bytes
from alembic import command
from alembic.config import Config
from playwright.sync_api import sync_playwright
import uvicorn
from app.config import get_settings, ROOT
from app.db import SessionLocal
from app.main import app
from app.schemas.forms import SubmissionForm, ScoreForm
from app.services.participation import join_contest, create_payment
from app.services.contest_state import change_state
from app.services.submissions import submit_project, save_score, publish_results
from app.services.storage import Storage


def main():
    port = 17773
    origin = f"http://127.0.0.1:{port}"
    get_settings().app_url = origin
    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    with SessionLocal() as db:
        admin_client, admin, _ = authenticated_client(db, 900001, "organizer")
        member_client, member, _ = authenticated_client(db, 100001, "developer")
        c = make_contest(
            db,
            title="Telegram Bot Challenge",
            summary="Создай бота, который помогает людям. От идеи до работающего проекта — один вызов.",
        )
        p = join_contest(db, c.id, member)
        receipt = Storage().save(png_bytes(), "receipt.png", "image/png", "receipts")
        create_payment(db, p.id, member, receipt)
        db.commit()
        active = make_contest(db, title="Automation Sprint", slug="automation-sprint", fee_tjs=0)
        running_p = join_contest(db, active.id, member)
        change_state(db, active.id, "start", admin.telegram_id)
        db.commit()
        result = make_contest(db, title="Build for Tomorrow", slug="build-for-tomorrow", fee_tjs=0)
        rp = join_contest(db, result.id, member)
        change_state(db, result.id, "start", admin.telegram_id)
        stored = Storage().save(zip_bytes(), "project.zip", "application/zip", "submissions")
        submission, _ = submit_project(
            db,
            rp.id,
            member,
            SubmissionForm(bot_username="project_bot", description="Проект помогает управлять очередью обращений."),
            stored,
        )
        change_state(db, result.id, "finish", admin.telegram_id)
        save_score(
            db,
            submission.id,
            admin.telegram_id,
            ScoreForm(
                functionality=38,
                ui=19,
                code_quality=19,
                stability=9,
                originality=10,
                comment="Удобный интерфейс и хорошая архитектура.",
            ),
        )
        publish_results(db, result.id, admin.telegram_id)
        judging = make_contest(db, slug="judging", title="Оценивание", fee_tjs=0)
        jp = join_contest(db, judging.id, member)
        change_state(db, judging.id, "start", admin.telegram_id)
        stored2 = Storage().save(zip_bytes(), "code.zip", "application/zip", "submissions")
        work, _ = submit_project(
            db,
            jp.id,
            member,
            SubmissionForm(bot_username="judging_bot", description="Работа для проверки формы оценивания."),
            stored2,
        )
        change_state(db, judging.id, "finish", admin.telegram_id)
        db.commit()
        ids = {
            "contest": c.id,
            "active": active.id,
            "payment_p": p.id,
            "running_p": running_p.id,
            "submission": work.id,
        }
        cookies = {
            "admin": admin_client.cookies.get("nexviro_session"),
            "member": member_client.cookies.get("nexviro_session"),
        }
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    out = Path(os.getenv("UI_ARTIFACT_DIR", "/tmp/nexviro-ui"))
    out.mkdir(parents=True, exist_ok=True)
    cases = {
        "guest": ["/", "/login", "/contests/telegram-bot-battle", "/contests/build-for-tomorrow/leaderboard"],
        "member": [
            "/profile",
            f"/payments/{ids['payment_p']}",
            f"/submissions/{ids['running_p']}",
            "/contests/build-for-tomorrow",
        ],
        "admin": [
            "/admin",
            "/admin/contests/new",
            f"/admin/contests/{ids['active']}",
            "/admin/payments",
            "/admin/submissions",
            f"/admin/submissions/{ids['submission']}",
            "/admin/activity",
        ],
    }
    failures = []
    checked = 0
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=os.getenv("CHROMIUM_EXECUTABLE") or None,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            for width in [1440, 390, 320]:
                for role, paths in cases.items():
                    context = browser.new_context(
                        viewport={"width": width, "height": 1000 if width > 1000 else 844}, device_scale_factor=1
                    )
                    if role in cookies:
                        context.add_cookies([{"name": "nexviro_session", "value": cookies[role], "url": origin}])
                    page = context.new_page()
                    page.on("pageerror", lambda error: failures.append(str(error)))
                    for path in paths:
                        response = page.goto(origin + path, wait_until="networkidle")
                        assert response.status == 200, (role, path, response.status)
                        overflow = page.evaluate("document.documentElement.scrollWidth > window.innerWidth")
                        if overflow:
                            failures.append(f"Horizontal overflow: {width} {role} {path}")
                        checked += 1
                        if path == "/" and width in [1440, 390]:
                            page.screenshot(path=str(out / f"home-{width}.png"), full_page=True)
                        if role == "admin" and path == "/admin/contests/new" and width == 390:
                            page.screenshot(path=str(out / "admin-form-390.png"), full_page=True)
                        if role == "admin" and path == f"/admin/submissions/{ids['submission']}":
                            page.locator("[name=functionality]").fill("37")
                            page.locator("[name=ui]").fill("18")
                            assert page.locator("[data-total]").inner_text() == "55"
                        if role == "admin" and path == f"/admin/contests/{ids['active']}":
                            page.get_by_role("button", name="Завершить приём работ").click()
                            assert page.locator("#confirm-dialog").is_visible()
                            page.locator("#confirm-dialog").get_by_role("button", name="Отмена", exact=True).click()
                            assert not page.locator("#confirm-dialog").is_visible()
                    context.close()
            browser.close()
        assert not failures, failures
        print(
            f"PASS: {checked} pages; widths 1440, 390, 320; no horizontal overflow or JS errors; score total and confirmation dialogs checked. Screenshots: {out}"
        )
    finally:
        server.should_exit = True
        thread.join(timeout=10)


if __name__ == "__main__":
    main()
