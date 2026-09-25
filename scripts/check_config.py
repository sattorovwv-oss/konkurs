"""Validate settings, schema revision, storage and database without printing secrets."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from app.config import get_settings, ROOT
from app.db import engine
from app.services.storage import Storage


def main():
    settings = get_settings()
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    if revision != head:
        raise RuntimeError("Выполните alembic upgrade head")
    storage = Storage()
    if storage.client:
        storage.client.head_bucket(Bucket=settings.s3_bucket)
    else:
        storage.root.mkdir(parents=True, exist_ok=True)
        import tempfile

        with tempfile.TemporaryFile(dir=storage.root) as temporary:
            temporary.write(b"check")
    print("OK: settings, schema, database and storage. No external messages sent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(
            f"Configuration check failed: {type(exc).__name__}. Check .env, DB access, migrations and storage.",
            file=sys.stderr,
        )
        sys.exit(1)
