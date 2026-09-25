from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import get_settings


class Base(DeclarativeBase):
    """Application metadata, managed exclusively by Alembic."""


settings = get_settings()
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=300,
    **(
        {"connect_args": {"check_same_thread": False}}
        if settings.database_url.startswith("sqlite")
        else {"pool_size": 5, "max_overflow": 5}
    ),
)
if engine.dialect.name == "sqlite":

    @event.listens_for(engine, "connect")
    def sqlite_fk(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
