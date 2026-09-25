from datetime import timedelta
from sqlalchemy import select, func
from app.models import Contest, Participation, Payment, Submission, Notification
from app.utils.time import now
from conftest import authenticated_client, make_contest, png_bytes, zip_bytes
from test_validation_and_ranking import valid_contest


def test_complete_two_participant_flow(db, admin_client, member_client):
    admin, admin_user, admin_csrf = admin_client
    a, user_a, csrf_a = member_client
    b, user_b, csrf_b = authenticated_client(db, 100002, "developer_b")
    form = valid_contest()
    form.update(csrf_token=admin_csrf, title="Complete Bot Battle", slug="complete-battle")
    response = admin.post("/admin/contests/new", data=form)
    assert response.status_code == 200, response.text
    c = db.scalar(select(Contest).where(Contest.slug == "complete-battle"))
    assert c.status == "draft"
    assert a.get("/contests/complete-battle").status_code == 404

    def action(name, **extra):
        return admin.post(f"/admin/contests/{c.id}/action", data={"csrf_token": admin_csrf, "action": name, **extra})

    assert action("publish").status_code == 200
    assert a.post("/contests/complete-battle/join", data={"csrf_token": csrf_a}).status_code == 200
    assert b.post("/contests/complete-battle/join", data={"csrf_token": csrf_b}).status_code == 200
    pa = db.scalar(select(Participation).where(Participation.user_id == user_a.id))
    pb = db.scalar(select(Participation).where(Participation.user_id == user_b.id))
    for client, p, csrf in [(a, pa, csrf_a), (b, pb, csrf_b)]:
        response = client.post(
            f"/payments/{p.id}", data={"csrf_token": csrf}, files={"receipt": ("check.png", png_bytes(), "image/png")}
        )
        assert response.status_code == 200, response.text
        payment = db.scalar(select(Payment).where(Payment.participation_id == p.id))
        data = {"csrf_token": admin_csrf, "action": "approve", "version": 1}
        response = admin.post(f"/admin/payments/{payment.id}/review", data=data)
        assert response.status_code == 200, response.text
        assert admin.post(f"/admin/payments/{payment.id}/review", data=data).status_code == 409
    db.expire_all()
    assert pa.participant_no == 1 and pb.participant_no == 2
    assert "Full task description" not in a.get("/contests/complete-battle").text
    assert a.get("/contests/complete-battle/status").json()["count"] == 2
    assert action("start").status_code == 200
    assert "Full task description" in a.get("/contests/complete-battle").text
    for client, p, csrf, bot in [(a, pa, csrf_a, "alpha_test_bot"), (b, pb, csrf_b, "bravo_test_bot")]:
        response = client.post(
            f"/submissions/{p.id}",
            data={
                "csrf_token": csrf,
                "bot_username": bot,
                "description": "Working project with documentation",
                "github_url": "",
            },
            files={"source": ("project.zip", zip_bytes(), "application/zip")},
        )
        assert response.status_code == 200, response.text
    sa = db.scalar(select(Submission).where(Submission.participation_id == pa.id))
    sb = db.scalar(select(Submission).where(Submission.participation_id == pb.id))
    assert a.get(f"/files/source/{sa.id}").content == zip_bytes()
    assert b.get(f"/files/source/{sa.id}").status_code == 404
    assert b.get(f"/submissions/{pa.id}").status_code == 404
    assert a.post(f"/admin/contests/{c.id}/action", data={"csrf_token": csrf_a, "action": "results"}).status_code == 403
    assert action("freeze", reason="Maintenance").status_code == 200
    edit = {"csrf_token": csrf_a, "bot_username": "alpha_test_bot", "description": "Updated final project"}
    assert a.post(f"/submissions/{pa.id}", data=edit).status_code == 409
    assert action("resume").status_code == 200
    assert a.post(f"/submissions/{pa.id}", data=edit).status_code == 200
    score_a = dict(
        csrf_token=admin_csrf,
        functionality=40,
        ui=20,
        code_quality=20,
        stability=10,
        originality=10,
        comment="Excellent architecture",
    )
    assert admin.post(f"/admin/submissions/{sa.id}/score", data=score_a).status_code == 422
    assert action("finish").status_code == 200
    assert a.post(f"/submissions/{pa.id}", data=edit).status_code == 409
    assert admin.post(f"/admin/submissions/{sa.id}/score", data=score_a).status_code == 200
    assert "Excellent architecture" not in a.get("/contests/complete-battle").text
    assert action("results").status_code == 409
    assert (
        admin.post(
            f"/admin/submissions/{sb.id}/score", data={**score_a, "functionality": 30, "comment": "Improve validation"}
        ).status_code
        == 200
    )
    assert action("results").status_code == 200
    assert action("results").status_code == 409
    assert admin.post(f"/admin/submissions/{sa.id}/score", data=score_a).status_code == 422
    result = a.get("/contests/complete-battle").text
    assert "Excellent architecture" in result and "Забрать приз" in result and "200" in result
    leaderboard = a.get("/contests/complete-battle/leaderboard")
    assert leaderboard.status_code == 200
    assert leaderboard.text.index("developer_a") < leaderboard.text.index("developer_b")
    for client in [a, b]:
        assert "Место #" in client.get("/profile").text
    for url in [
        "/admin",
        "/admin/contests",
        "/admin/payments",
        "/admin/submissions",
        "/admin/activity",
        f"/admin/submissions/{sa.id}",
    ]:
        assert admin.get(url).status_code == 200, url
    for event in ["result:%", "start:%", "payment:%"]:
        assert db.scalar(select(func.count(Notification.id)).where(Notification.event_key.like(event))) == 2
    b.close()


def test_bad_form_errors_inline_preserve_values(admin_client):
    admin, user, csrf = admin_client
    data = valid_contest()
    data.update(csrf_token=csrf, ends_at="2020-01-01T12:30")
    response = admin.post("/admin/contests/new", data=data)
    assert response.status_code == 422
    assert "Дедлайн должен быть позже старта" in response.text
    assert 'value="Bot Battle"' in response.text
    assert response.headers["content-type"].startswith("text/html")


def test_csrf_admin_access_and_error_html(client, member_client, admin_client, db):
    member, user, csrf = member_client
    admin, _, admin_csrf = admin_client
    c = make_contest(db)
    assert client.get("/admin").status_code == 401
    for url in ["/admin", "/admin/payments", "/admin/contests/new", "/admin/activity"]:
        response = member.get(url)
        assert response.status_code == 403
        assert response.headers["content-type"].startswith("text/html")
    assert member.post("/contests/telegram-bot-battle/join", data={}).status_code == 403
    assert (
        member.post(
            "/contests/telegram-bot-battle/join", data={"csrf_token": csrf}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        admin.post(
            f"/admin/contests/{c.id}/action", data={"csrf_token": "wrong", "action": "cancel", "reason": "bad"}
        ).status_code
        == 403
    )
    assert client.get("/does-not-exist").status_code == 404
    assert client.get("/profile").status_code == 401
    assert client.get("/static/../../.env").status_code == 404


def test_cancelled_refund_page_no_payments_or_results(db, admin_client, member_client):
    admin, _, ac = admin_client
    member, user, csrf = member_client
    c = make_contest(db, fee_tjs=0)
    assert member.post(f"/contests/{c.slug}/join", data={"csrf_token": csrf}).status_code == 200
    assert (
        admin.post(
            f"/admin/contests/{c.id}/action",
            data={"csrf_token": ac, "action": "cancel", "reason": "Cancelled by organizer"},
        ).status_code
        == 200
    )
    assert "возврате" in member.get(f"/contests/{c.slug}").text
    assert admin.post(f"/admin/contests/{c.id}/action", data={"csrf_token": ac, "action": "results"}).status_code == 409
    p = db.scalar(select(Participation).where(Participation.user_id == user.id))
    assert (
        member.post(
            f"/payments/{p.id}", data={"csrf_token": csrf}, files={"receipt": ("check.png", png_bytes(), "image/png")}
        ).status_code
        == 409
    )


def test_payment_revision_reject_reupload_and_idor(db, admin_client, member_client):
    admin, _, ac = admin_client
    member, user, csrf = member_client
    c = make_contest(db)
    member.post(f"/contests/{c.slug}/join", data={"csrf_token": csrf})
    p = db.scalar(select(Participation).where(Participation.user_id == user.id))
    for attempt in [1, 2]:
        assert (
            member.post(
                f"/payments/{p.id}",
                data={"csrf_token": csrf},
                files={"receipt": ("check.png", png_bytes(), "image/png")},
            ).status_code
            == 200
        )
        db.expire_all()
        pay = db.scalar(select(Payment).where(Payment.participation_id == p.id))
        assert pay.version == attempt
        if attempt == 1:
            assert (
                admin.post(
                    f"/admin/payments/{pay.id}/review",
                    data={"csrf_token": ac, "action": "reject", "version": 1, "reason": "Unreadable"},
                ).status_code
                == 200
            )
    assert (
        admin.post(
            f"/admin/payments/{pay.id}/review", data={"csrf_token": ac, "action": "approve", "version": 1}
        ).status_code
        == 409
    )
    assert (
        admin.post(
            f"/admin/payments/{pay.id}/review", data={"csrf_token": ac, "action": "approve", "version": 2}
        ).status_code
        == 200
    )
    other, _, oc = authenticated_client(db, 123456, "other")
    assert other.get(f"/payments/{p.id}").status_code == 404
    assert other.get(f"/files/receipt/{pay.id}").status_code == 404
    other.close()


def test_server_deadline_without_scheduler(db, member_client):
    member, user, csrf = member_client
    c = make_contest(db, fee_tjs=0)
    member.post(f"/contests/{c.slug}/join", data={"csrf_token": csrf})
    p = db.scalar(select(Participation).where(Participation.user_id == user.id))
    c.registration_deadline = now() - timedelta(hours=4)
    c.starts_at = now() - timedelta(hours=3)
    c.ends_at = now() - timedelta(seconds=1)
    c.status = "running"
    c.actual_started_at = c.starts_at
    db.commit()
    response = member.post(
        f"/submissions/{p.id}",
        data={"csrf_token": csrf, "bot_username": "deadline_bot", "description": "Very late submission"},
        files={"source": ("project.zip", zip_bytes(), "application/zip")},
    )
    assert response.status_code == 409
    assert not db.scalar(select(Submission.id))


def test_xss_escaped_and_unpublished_task_hidden(db, client, admin_client):
    c = make_contest(
        db, title="<script>alert(1)</script>", summary="<img src=x onerror=alert(1)>", full_task="HIDDEN_SOURCE_TEST"
    )
    response = client.get(f"/contests/{c.slug}")
    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "HIDDEN_SOURCE_TEST" not in response.text
    assert "script-src" in response.headers["content-security-policy"]
    assert response.headers["cache-control"] == "private, no-store"
    assert "HIDDEN_SOURCE_TEST" in admin_client[0].get(f"/contests/{c.slug}").text


def test_body_limit_and_secure_cookie_setting(client):
    from app.config import get_settings

    assert get_settings().model_copy(update={"app_url": "https://nexvirobattle.cfd"}).secure_cookie
    response = client.post("/payments/1", content=b"", headers={"Content-Length": str(40 * 1024**2)})
    assert response.status_code == 413
    assert "Файл слишком большой" in response.text


def test_oauth_access_log_redaction():
    import logging
    from app.utils.logging import RedactOAuthQuery

    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        "%s %s %s %s %s",
        ("127.0.0.1", "GET", "/auth/telegram/callback?code=secret&state=secret", "1.1", 303),
        None,
    )
    RedactOAuthQuery().filter(record)
    assert "secret" not in record.getMessage()
