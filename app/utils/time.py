from datetime import datetime, timezone
from zoneinfo import ZoneInfo

DUSHANBE = ZoneInfo("Asia/Dushanbe")
UTC = timezone.utc


def now() -> datetime:
    return datetime.now(UTC)


def aware(value: datetime) -> datetime:
    # SQLite test adapter drops offsets; PostgreSQL always returns aware values.
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def from_local(value: str | datetime) -> datetime:
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.tzinfo is None:
        value = value.replace(tzinfo=DUSHANBE)
    return value.astimezone(UTC)


def local_display(value: datetime | None) -> str:
    return aware(value).astimezone(DUSHANBE).strftime("%d.%m.%Y · %H:%M") if value else "—"


def local_input(value: datetime | None) -> str:
    return aware(value).astimezone(DUSHANBE).strftime("%Y-%m-%dT%H:%M") if value else ""
