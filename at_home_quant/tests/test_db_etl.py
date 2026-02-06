import datetime
import importlib

import pandas as pd
import pytest

from at_home_quant.data import fetcher
from at_home_quant.data.tickers import ALL_TICKERS, TickerInfo


@pytest.fixture()
def temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("DATA_MODE", "research")
    session_module = importlib.reload(importlib.import_module("at_home_quant.db.session"))
    models = importlib.reload(importlib.import_module("at_home_quant.db.models"))
    crud = importlib.reload(importlib.import_module("at_home_quant.db.crud"))
    session_module.init_db()
    return session_module, crud, models


def test_roundtrip_prices(temp_db):
    session_module, crud, models = temp_db
    start = datetime.date.today() - datetime.timedelta(days=45)
    symbol = "SPY"
    df = fetcher.fetch_price_history(symbol, start=start)
    df = fetcher.compute_returns(df)

    with session_module.get_session() as session:
        crud.upsert_tickers(session, {symbol: ALL_TICKERS[symbol]})
        crud.upsert_prices(session, df)

    with session_module.get_session() as session:
        count = session.query(models.PriceDaily).count()
        assert count == len(df)
        latest = (
            session.query(models.PriceDaily)
            .join(models.Ticker)
            .filter(models.Ticker.symbol == symbol)
            .order_by(models.PriceDaily.date.desc())
            .first()
        )
        assert latest is not None
        assert latest.adj_close is not None


def test_upsert_prices_large_batch_does_not_exceed_sqlite_variable_limit(temp_db):
    session_module, crud, models = temp_db
    symbol = "SPY"
    dates = pd.bdate_range(start="2024-01-01", periods=300)
    closes = pd.Series(range(100, 400), dtype=float)
    df = pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "open": closes - 0.5,
            "high": closes + 0.5,
            "low": closes - 1.0,
            "close": closes,
            "adj_close": closes,
            "volume": 1_000_000,
            "return_": closes.pct_change().fillna(0.0),
        }
    )

    with session_module.get_session() as session:
        crud.upsert_tickers(session, {symbol: ALL_TICKERS[symbol]})
        crud.upsert_prices(session, df)

    with session_module.get_session() as session:
        count = session.query(models.PriceDaily).count()
        assert count == len(df)


def test_upsert_prices_fills_missing_adj_close_from_close(temp_db):
    session_module, crud, models = temp_db
    symbol = "SPY"
    dates = pd.bdate_range(start="2024-01-01", periods=3)
    df = pd.DataFrame(
        {
            "date": dates,
            "symbol": symbol,
            "open": [100.0, 101.0, 102.0],
            "high": [101.0, 102.0, 103.0],
            "low": [99.0, 100.0, 101.0],
            "close": [100.5, 101.5, None],
            "adj_close": [None, None, None],
            "volume": [1_000, 1_100, 1_200],
            "return_": [0.0, 0.01, 0.0],
        }
    )

    with session_module.get_session() as session:
        crud.upsert_tickers(session, {symbol: ALL_TICKERS[symbol]})
        crud.upsert_prices(session, df)

    with session_module.get_session() as session:
        rows = (
            session.query(models.PriceDaily)
            .join(models.Ticker)
            .filter(models.Ticker.symbol == symbol)
            .order_by(models.PriceDaily.date)
            .all()
        )
        # One row with both close/adj_close missing is skipped.
        assert len(rows) == 2
        assert rows[0].adj_close == rows[0].close == 100.5
        assert rows[1].adj_close == rows[1].close == 101.5
