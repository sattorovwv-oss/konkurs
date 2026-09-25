from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True)
    app_name: str = "NEXVIRO Code Battle"
    app_env: str = "production"
    app_url: str = "https://nexvirobattle.cfd"
    secret_key: str = Field(min_length=32)
    database_url: str
    bot_token: str = ""
    bot_username: str = ""
    telegram_client_id: str = ""
    telegram_client_secret: str = ""
    admin_telegram_ids: str = ""
    admin_chat_id: int | None = None
    payment_card_number: str = ""
    payment_card_holder: str = ""
    prize_contact_url: str = ""
    storage_backend: str = "local"
    storage_root: Path = ROOT / "storage"
    s3_endpoint_url: str = ""
    s3_region: str = "auto"
    s3_bucket: str = ""
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    max_receipt_mb: int = Field(default=8, ge=1, le=20)
    max_source_mb: int = Field(default=30, ge=1, le=30)
    session_days: int = Field(default=7, ge=1, le=30)
    scheduler_enabled: bool = True
    trusted_hosts: str = "nexvirobattle.cfd,www.nexvirobattle.cfd,localhost,127.0.0.1"

    @property
    def admin_ids(self) -> set[int]:
        return {int(i.strip()) for i in self.admin_telegram_ids.split(",") if i.strip()}

    @property
    def callback_url(self) -> str:
        return self.app_url.rstrip("/") + "/auth/telegram/callback"

    @property
    def secure_cookie(self) -> bool:
        return self.app_url.startswith("https://")

    @model_validator(mode="after")
    def check_configuration(self):
        parsed = urlsplit(self.app_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("APP_URL должен быть полным http(s) URL")
        if self.app_env not in {"production", "development", "test"}:
            raise ValueError("APP_ENV: production / development / test")
        if self.app_env == "production":
            required = (
                "bot_token",
                "bot_username",
                "telegram_client_id",
                "telegram_client_secret",
                "admin_telegram_ids",
                "admin_chat_id",
                "payment_card_number",
                "payment_card_holder",
                "prize_contact_url",
            )
            missing = [x.upper() for x in required if not getattr(self, x)]
            if missing:
                raise ValueError("Заполните переменные: " + ", ".join(missing))
            if not self.secure_cookie:
                raise ValueError("В production APP_URL должен использовать HTTPS")
            if not self.database_url.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
                raise ValueError("Production требует PostgreSQL")
        if not all(i > 0 for i in self.admin_ids):
            raise ValueError("ADMIN_TELEGRAM_IDS: положительные ID через запятую")
        if self.prize_contact_url and not self.prize_contact_url.startswith("https://"):
            raise ValueError("PRIZE_CONTACT_URL должен использовать HTTPS")
        if self.storage_backend not in {"local", "s3"}:
            raise ValueError("STORAGE_BACKEND: local / s3")
        if self.storage_backend == "s3" and not all((self.s3_bucket, self.s3_access_key_id, self.s3_secret_access_key)):
            raise ValueError("Заполните S3_BUCKET, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY")
        if self.database_url.startswith("postgres://"):
            self.database_url = self.database_url.replace("postgres://", "postgresql+psycopg://", 1)
        elif self.database_url.startswith("postgresql://"):
            self.database_url = self.database_url.replace("postgresql://", "postgresql+psycopg://", 1)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
