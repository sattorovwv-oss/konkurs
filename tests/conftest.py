import base64
import io
import json
import os
import secrets
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from itsdangerous import TimestampSigner
from PIL import Image

TEST_ROOT = Path(tempfile.mkdtemp(prefix="nexviro-tests-"))
os.environ.update(
    APP_ENV="test",
    APP_URL="http://testserver",
    SECRET_KEY="test-only-not-a-production-key-000000",
    DATABASE_URL=os.getenv("TEST_DATABASE_URL", f"sqlite:///{TEST_ROOT / 'test.db'}"),
    STORAGE_BACKEND="local",
    STORAGE_ROOT=str(TEST_ROOT / "storage"),
    ADMIN_TELEGRAM_IDS="900001,900002",
    ADMIN_CHAT_ID="-900001",
    BOT_TOKEN="",
    BOT_USERNAME="nexviro_test_bot",
    TELEGRAM_CLIENT_ID="12345",
    TELEGRAM_CLIENT_SECRET="test-client-secret",
    PAYMENT_CARD_NUMBER="TEST CARD",
    PAYMENT_CARD_HOLDER="Test Organizer",
    PRIZE_CONTACT_URL="https://t.me/test_organizer",
    SCHEDULER_ENABLED="false",
    TRUSTED_HOSTS="testserver,localhost,127.0.0.1",
)

from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User, LoginSession, Contest  # noqa: E402
from app.services.telegram_auth import digest  # noqa: E402
from app.utils.time import now  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    if engine.dialect.name == "postgresql" and engine.url.database != "nexviro_test":
        raise RuntimeError("Tests only allow a PostgreSQL database named nexviro_test")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "head")
    yield
    engine.dispose()


@pytest.fixture(autouse=True)
def clean_database(migrated_database):
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(table.delete())
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


def authenticated_client(db, telegram_id=100001, username="developer_a"):
    user = User(telegram_id=telegram_id, first_name=username, username=username)
    db.add(user)
    db.flush()
    token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
    db.add(LoginSession(token_hash=digest(token), user_id=user.id, expires_at=now() + timedelta(days=1)))
    db.commit()
    session = base64.b64encode(json.dumps({"sid": token, "csrf": csrf}).encode())
    cookie = TimestampSigner(get_settings().secret_key).sign(session).decode()
    client = TestClient(app)
    client.cookies.set("nexviro_session", cookie)
    return client, user, csrf


@pytest.fixture
def admin_client(db):
    client, user, csrf = authenticated_client(db, 900001, "organizer")
    yield client, user, csrf
    client.close()


@pytest.fixture
def member_client(db):
    client, user, csrf = authenticated_client(db)
    yield client, user, csrf
    client.close()


def make_contest(db, **overrides):
    data = dict(
        title="Telegram Bot Battle",
        slug="telegram-bot-battle",
        summary="Создание полезного Telegram бота.",
        full_task="SECRET TASK: build a support bot with a queue",
        rules="Выполните задание самостоятельно.",
        max_participants=10,
        fee_tjs=50,
        prize_first_tjs=200,
        prize_second_tjs=100,
        prize_third_tjs=50,
        registration_deadline=now() + timedelta(hours=1),
        starts_at=now() + timedelta(hours=2),
        ends_at=now() + timedelta(hours=4),
        published=True,
        status="scheduled",
    )
    data.update(overrides)
    c = Contest(**data)
    db.add(c)
    db.commit()
    return c


def png_bytes():
    target = io.BytesIO()
    Image.new("RGB", (16, 16), "#33cc99").save(target, format="PNG")
    return target.getvalue()


def zip_bytes(name="main.py", data=b'print("Hello Code Battle")\n'):
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)
    return target.getvalue()
