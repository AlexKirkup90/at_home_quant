import importlib
import sqlite3

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError


def test_init_db_upgrades_legacy_weekly_recommendation_batch_schema(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE weekly_recommendation_batches (
            id INTEGER PRIMARY KEY,
            created_at DATETIME NOT NULL,
            as_of_date DATE NOT NULL,
            best_universe VARCHAR NOT NULL,
            best_universe_score FLOAT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    session_module = importlib.reload(importlib.import_module("at_home_quant.db.session"))
    session_module.init_db()

    inspector = inspect(session_module.engine)
    columns = {col["name"] for col in inspector.get_columns("weekly_recommendation_batches")}
    assert "status" in columns
    assert "data_snapshot_hash" in columns
    assert "watchlist_json" in columns


def test_init_db_retries_transient_sqlite_lock(monkeypatch, tmp_path):
    db_path = tmp_path / "locked.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    session_module = importlib.reload(importlib.import_module("at_home_quant.db.session"))

    calls = {"count": 0}
    original_create_all = session_module.Base.metadata.create_all

    def flaky_create_all(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise OperationalError("CREATE TABLE ...", {}, Exception("database is locked"))
        return original_create_all(*args, **kwargs)

    monkeypatch.setattr(session_module.Base.metadata, "create_all", flaky_create_all)
    session_module.init_db(retries=2, delay_seconds=0.0)

    assert calls["count"] >= 2
