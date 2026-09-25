import hmac
import secrets
from fastapi import Depends, Request
from sqlalchemy import select
from app.config import get_settings
from app.db import get_db
from app.models import LoginSession, User
from app.services.errors import DomainError
from app.services.telegram_auth import digest
from app.utils.time import now


def is_admin(user):
    return bool(user and user.telegram_id in get_settings().admin_ids)


def current_user(request: Request, db=Depends(get_db)):
    token = request.session.get("sid")
    if not token:
        return None
    return db.scalar(
        select(User)
        .join(LoginSession, LoginSession.user_id == User.id)
        .where(LoginSession.token_hash == digest(token), LoginSession.expires_at > now())
    )


def require_user(user=Depends(current_user)):
    if not user:
        raise DomainError("Войдите через Telegram, чтобы продолжить.", 401)
    return user


def require_admin(user=Depends(require_user)):
    if not is_admin(user):
        raise DomainError("Эта страница доступна только администратору.", 403)
    return user


def csrf_token(request):
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


async def safe_form(request: Request):
    form = await request.form(max_files=1, max_fields=50, max_part_size=100_000)
    token = form.get("csrf_token", "")
    expected = request.session.get("csrf", "")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != get_settings().app_url.rstrip("/"):
        raise DomainError("Запрос отправлен с другого сайта. Откройте форму заново.", 403)
    if not isinstance(token, str) or not expected or not hmac.compare_digest(token, expected):
        raise DomainError("Форма устарела. Обновите страницу и повторите действие.", 403)
    return form
