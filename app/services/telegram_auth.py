import base64
import hashlib
import hmac
import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import select

from app.config import get_settings
from app.models import OAuthAttempt, User, LoginSession
from app.services.errors import DomainError
from app.utils.time import now, aware

ISSUER = "https://oauth.telegram.org"
JWKS_URL = ISSUER + "/.well-known/jwks.json"
_jwks = {"keys": [], "expires": 0.0}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def pkce_challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def start_login(db, session):
    s = get_settings()
    if not s.telegram_client_id or not s.telegram_client_secret:
        raise DomainError("Вход через Telegram ещё не настроен организатором.", 503)
    state, browser, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    db.add(
        OAuthAttempt(
            state_hash=digest(state),
            browser_hash=digest(browser),
            verifier=verifier,
            expires_at=now() + timedelta(minutes=10),
        )
    )
    db.commit()
    session["oauth_browser"] = browser
    return (
        ISSUER
        + "/auth?"
        + urlencode(
            {
                "client_id": s.telegram_client_id,
                "redirect_uri": s.callback_url,
                "response_type": "code",
                "scope": "openid profile telegram:bot_access",
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
    )


def consume_attempt(db, session, state):
    browser = session.pop("oauth_browser", "")
    if not state or not browser or len(state) > 200:
        raise DomainError("Вход устарел. Начните авторизацию заново.", 400)
    attempt = db.scalar(select(OAuthAttempt).where(OAuthAttempt.state_hash == digest(state)).with_for_update())
    if (
        not attempt
        or aware(attempt.expires_at) <= now()
        or not hmac.compare_digest(attempt.browser_hash, digest(browser))
    ):
        raise DomainError("Не удалось проверить запрос входа. Попробуйте снова.", 400)
    verifier = attempt.verifier
    db.delete(attempt)
    db.commit()  # Consume once even if exchange fails. Replay cannot reuse the code.
    return verifier


async def get_jwks(force=False):
    if not force and _jwks["expires"] > time.monotonic():
        return _jwks["keys"]
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        response = await client.get(JWKS_URL)
        response.raise_for_status()
        keys = response.json()["keys"]
    _jwks.update(keys=keys, expires=time.monotonic() + 3600)
    return keys


def validate_id_token(token, keys, client_id):
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256":
            raise ValueError("Only configured RS256 is accepted")
        key = next(k for k in keys if k.get("kid") == header.get("kid") and k.get("kty") == "RSA")
        claims = jwt.decode(
            token,
            jwt.PyJWK.from_dict(key).key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=ISSUER,
            leeway=30,
            options={"require": ["iss", "aud", "sub", "exp", "iat", "id"]},
        )
        if isinstance(claims["id"], bool) or not str(claims["id"]).isdigit() or not 0 < int(claims["id"]) < 2**63:
            raise ValueError("Invalid Telegram id")
        if claims.get("azp", client_id) != client_id:
            raise ValueError("Invalid authorized party")
        if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and claims.get("azp") != client_id:
            raise ValueError("Missing authorized party")
        if not isinstance(claims["sub"], str) or not 1 <= len(claims["sub"]) <= 255:
            raise ValueError("Invalid subject")
        return claims
    except (jwt.PyJWTError, StopIteration, ValueError, KeyError, TypeError) as exc:
        raise DomainError("Telegram не подтвердил подлинность входа. Попробуйте снова.", 401) from exc


async def exchange_code(code, verifier):
    s = get_settings()
    if not code or len(code) > 4096:
        raise DomainError("Telegram не вернул код авторизации.", 400)
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as client:
            response = await client.post(
                ISSUER + "/token",
                auth=(s.telegram_client_id, s.telegram_client_secret),
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": s.callback_url,
                    "client_id": s.telegram_client_id,
                    "code_verifier": verifier,
                },
            )
            response.raise_for_status()
            token = response.json()["id_token"]
        keys = await get_jwks()
        header = jwt.get_unverified_header(token)
        if not any(k.get("kid") == header.get("kid") for k in keys):
            keys = await get_jwks(force=True)
        return validate_id_token(token, keys, s.telegram_client_id)
    except DomainError:
        raise
    except (httpx.HTTPError, KeyError, ValueError, jwt.PyJWTError) as exc:
        raise DomainError("Сервис Telegram временно недоступен. Попробуйте войти ещё раз.", 502) from exc


def finish_login(db, session, claims):
    user_id = int(claims["id"])  # OIDC sub is NOT the numeric Telegram ID.
    values = {
        "telegram_id": user_id,
        "oidc_subject": claims["sub"],
        "username": str(claims.get("preferred_username") or "")[:64] or None,
        "first_name": str(claims.get("given_name") or claims.get("name") or "Участник")[:255],
        "last_name": str(claims.get("family_name") or "")[:255] or None,
        "photo_url": str(claims.get("picture") or "")[:2048] or None,
        "updated_at": now(),
    }
    if values["photo_url"] and not values["photo_url"].startswith("https://"):
        values["photo_url"] = None
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    stmt = insert(User).values(**values)
    db.execute(
        stmt.on_conflict_do_update(
            index_elements=[User.telegram_id], set_={k: v for k, v in values.items() if k != "telegram_id"}
        )
    )
    user = db.scalar(select(User).where(User.telegram_id == user_id).execution_options(populate_existing=True))
    old_token = session.get("sid")
    if old_token:
        old = db.get(LoginSession, digest(old_token))
        if old:
            db.delete(old)
    token = secrets.token_urlsafe(48)
    db.add(
        LoginSession(
            token_hash=digest(token), user_id=user.id, expires_at=now() + timedelta(days=get_settings().session_days)
        )
    )
    db.commit()
    session.clear()
    session.update(sid=token, csrf=secrets.token_urlsafe(32))
    return user
