import datetime
import importlib
from contextlib import contextmanager

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerType
import at_home_quant.etl.daily_update as daily_update_module
from at_home_quant.db.models import Base
from at_home_quant.etl.daily_update import _load_symbol_universe


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    session_module = importlib.reload(importlib.import_module("at_home_quant.db.session"))
    models = importlib.reload(importlib.import_module("at_home_quant.db.models"))
    session_module.init_db()
    return session_module, models


def test_load_symbol_universe_includes_dynamic_db_tickers(temp_db):
    session_module, models = temp_db
    with session_module.get_session() as session:
        session.add(
            models.Ticker(
                symbol="CUSTOM",
                name="Custom Holding",
                asset_type=TickerType.EQUITY,
                universe=None,
                currency="USD",
            )
        )

    with session_module.get_session() as session:
        symbols = _load_symbol_universe(session)

    assert "CUSTOM" in symbols
    assert "SPY" in symbols


def test_run_daily_update_handles_dynamic_tickers_without_keyerror(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as seed_session:
        seed_session.add(
            daily_update_module.Ticker(
                symbol="CUSTOM",
                name="Custom Holding",
                asset_type=TickerType.EQUITY,
                universe=None,
                currency="USD",
            )
        )
        seed_session.commit()

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    captured_symbols: list[str] = []

    def fetch_override(symbols, start=None, end=None):
        if symbols:
            captured_symbols.extend(symbols)
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(datetime.date.today())],
                "symbol": [symbols[0]],
                "open": [100.0],
                "high": [101.0],
                "low": [99.0],
                "close": [100.0],
                "adj_close": [100.0],
                "volume": [1_000_000],
            }
        )

    monkeypatch.setattr(daily_update_module, "get_session", get_session_override)
    monkeypatch.setattr(daily_update_module, "init_db", lambda: None)
    monkeypatch.setattr(daily_update_module, "fetch_prices_for_universe", fetch_override)

    daily_update_module.run_daily_update()

    assert "CUSTOM" in captured_symbols
