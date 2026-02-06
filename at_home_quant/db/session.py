from contextlib import contextmanager
import time
from typing import Iterator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from at_home_quant.config.settings import ensure_data_dir_exists, get_settings
from at_home_quant.db.models import Base


settings = get_settings()
ensure_data_dir_exists(settings.database_url)
engine_kwargs = {"future": True}
if settings.database_url.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"timeout": 30}
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@event.listens_for(engine, "connect")
def _configure_sqlite_connection(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
    if engine.dialect.name != "sqlite":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        # Best-effort tuning; do not fail connection setup if pragma is unsupported.
        pass
    finally:
        cursor.close()


def _apply_sqlite_migrations() -> None:
    """Apply lightweight schema upgrades for existing SQLite databases."""
    if engine.dialect.name != "sqlite":
        return

    inspector = inspect(engine)
    if "weekly_recommendation_batches" not in inspector.get_table_names():
        return

    existing_columns = {col["name"] for col in inspector.get_columns("weekly_recommendation_batches")}
    statements: list[str] = []
    if "status" not in existing_columns:
        statements.append(
            "ALTER TABLE weekly_recommendation_batches "
            "ADD COLUMN status VARCHAR NOT NULL DEFAULT 'open'"
        )
    if "data_snapshot_hash" not in existing_columns:
        statements.append(
            "ALTER TABLE weekly_recommendation_batches "
            "ADD COLUMN data_snapshot_hash VARCHAR"
        )
    if "watchlist_json" not in existing_columns:
        statements.append(
            "ALTER TABLE weekly_recommendation_batches "
            "ADD COLUMN watchlist_json TEXT NOT NULL DEFAULT '[]'"
        )

    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS "
                "ix_weekly_recommendation_batches_data_snapshot_hash "
                "ON weekly_recommendation_batches (data_snapshot_hash)"
            )
        )


def _is_sqlite_lock_error(exc: OperationalError) -> bool:
    if engine.dialect.name != "sqlite":
        return False
    return "database is locked" in str(exc).lower() or "database schema is locked" in str(exc).lower()


def init_db(retries: int = 5, delay_seconds: float = 0.2) -> None:
    last_error: OperationalError | None = None
    for attempt in range(retries + 1):
        try:
            Base.metadata.create_all(bind=engine)
            _apply_sqlite_migrations()
            return
        except OperationalError as exc:
            if not _is_sqlite_lock_error(exc):
                raise
            last_error = exc
            if attempt >= retries:
                raise
            time.sleep(delay_seconds * (2**attempt))
    if last_error is not None:
        raise last_error


@contextmanager
def get_session() -> Iterator[Session]:
    session: Session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = ["engine", "SessionLocal", "init_db", "get_session"]
