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
