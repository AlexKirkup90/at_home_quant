from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from at_home_quant.config.settings import ensure_data_dir_exists, get_settings
from at_home_quant.db.models import Base


settings = get_settings()
ensure_data_dir_exists(settings.database_url)
engine = create_engine(settings.database_url, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


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


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    _apply_sqlite_migrations()


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
