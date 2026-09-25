import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy import delete, text
from sqlalchemy.exc import IntegrityError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.staticfiles import StaticFiles

from app.config import ROOT, get_settings
from app.db import SessionLocal
from app.models import LoginSession, OAuthAttempt
from app.routers import auth, contests, payments, submissions, leaderboard, admin, files
from app.services.contest_state import tick
from app.services.errors import DomainError
from app.utils.middleware import RequestGuard
from app.utils.logging import RedactOAuthQuery
from app.utils.time import now
from app.utils.web import render

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Never log HTTP URLs containing OAuth codes, database credentials or Bot tokens.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)
logging.getLogger("uvicorn.access").addFilter(RedactOAuthQuery())
settings = get_settings()


def scheduler_tick():
    with SessionLocal() as db:
        tick(db)
        db.execute(delete(OAuthAttempt).where(OAuthAttempt.expires_at < now()))
        db.execute(delete(LoginSession).where(LoginSession.expires_at < now()))
        db.commit()


async def scheduler(stop):
    while not stop.is_set():
        try:
            await run_in_threadpool(scheduler_tick)
        except Exception as exc:
            logger.error("Scheduler error: %s", type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app):
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler(stop)) if settings.scheduler_enabled else None
    yield
    stop.set()
    if task:
        await task


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="nexviro_session",
    same_site="lax",
    https_only=settings.secure_cookie,
    max_age=settings.session_days * 86400,
)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=[x.strip() for x in settings.trusted_hosts.split(",") if x.strip()]
)
app.add_middleware(
    RequestGuard,
    maximum=(max(settings.max_source_mb, settings.max_receipt_mb) + 1) * 1024**2,
    secure=settings.secure_cookie,
)
app.mount("/static", StaticFiles(directory=ROOT / "app/static"), name="static")
for router in (
    auth.router,
    contests.router,
    payments.router,
    submissions.router,
    leaderboard.router,
    admin.router,
    files.router,
):
    app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def health():
    with SessionLocal() as db:
        db.execute(text("SELECT 1"))
        db.execute(text("SELECT version_num FROM alembic_version"))
    return {"status": "ok"}


@app.exception_handler(DomainError)
async def domain_error(request: Request, exc: DomainError):
    return render(request, "error.html", status_code=exc.status, code=exc.status, message=exc.message)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc):
    return render(
        request, "error.html", status_code=422, code=422, message="Проверьте заполненные поля и формат данных."
    )


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc):
    messages = {
        404: "Страница не найдена.",
        405: "Этот способ обращения к странице не поддерживается.",
        403: "Доступ запрещён.",
        400: "Не удалось прочитать запрос.",
    }
    return render(
        request,
        "error.html",
        status_code=exc.status_code,
        code=exc.status_code,
        message=messages.get(exc.status_code, "Не удалось выполнить запрос."),
    )


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc):
    logger.warning("Database integrity conflict")
    return render(
        request,
        "error.html",
        status_code=409,
        code=409,
        message="Данные уже изменились. Обновите страницу и повторите действие.",
    )


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc):
    logger.error("Request failed: %s path=%s", type(exc).__name__, request.url.path)
    return render(
        request,
        "error.html",
        status_code=500,
        code=500,
        message="Не удалось завершить операцию. Попробуйте снова или свяжитесь с организатором.",
    )
