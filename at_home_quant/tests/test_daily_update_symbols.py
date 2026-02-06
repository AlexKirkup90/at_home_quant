import importlib

import pytest

from at_home_quant.data.tickers import TickerType
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
