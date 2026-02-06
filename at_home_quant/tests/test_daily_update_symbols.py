import datetime
import importlib
from contextlib import contextmanager

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.health import PORTFOLIO_REQUIRED_SYMBOLS
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


def test_run_daily_update_reuses_last_date_for_incremental_fetch(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    today = datetime.date.today()
    last_date = today - datetime.timedelta(days=1)

    with Session(engine) as seed_session:
        seed_session.add(
            daily_update_module.Ticker(
                symbol="SPY",
                name="S&P 500 ETF",
                asset_type=TickerType.ETF,
                universe=None,
                currency="USD",
            )
        )
        seed_session.flush()
        ticker_id = seed_session.query(daily_update_module.Ticker.id).scalar()
        seed_session.add(
            daily_update_module.PriceDaily(
                ticker_id=ticker_id,
                date=last_date,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                adj_close=100.0,
                volume=1_000_000,
                return_=0.0,
            )
        )
        seed_session.commit()

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    captured_starts: list[datetime.date] = []

    def fetch_override(symbols, start=None, end=None):
        if symbols == ["SPY"]:
            captured_starts.append(start)
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(today)],
                "symbol": [symbols[0]],
                "open": [101.0],
                "high": [102.0],
                "low": [100.0],
                "close": [101.0],
                "adj_close": [101.0],
                "volume": [1_000_000],
            }
        )

    monkeypatch.setattr(daily_update_module, "get_session", get_session_override)
    monkeypatch.setattr(daily_update_module, "init_db", lambda: None)
    monkeypatch.setattr(daily_update_module, "_load_symbol_universe", lambda _session: ["SPY"])
    monkeypatch.setattr(daily_update_module, "fetch_prices_for_universe", fetch_override)

    daily_update_module.run_daily_update()

    assert captured_starts == [last_date]


def test_run_daily_update_skips_symbol_when_latest_date_is_today(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    today = datetime.date.today()

    with Session(engine) as seed_session:
        seed_session.add(
            daily_update_module.Ticker(
                symbol="SPY",
                name="S&P 500 ETF",
                asset_type=TickerType.ETF,
                universe=None,
                currency="USD",
            )
        )
        seed_session.flush()
        ticker_id = seed_session.query(daily_update_module.Ticker.id).scalar()
        seed_session.add(
            daily_update_module.PriceDaily(
                ticker_id=ticker_id,
                date=today,
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                adj_close=100.0,
                volume=1_000_000,
                return_=0.0,
            )
        )
        seed_session.commit()

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    fetch_called = False

    def fetch_override(symbols, start=None, end=None):  # noqa: ARG001
        nonlocal fetch_called
        fetch_called = True
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(today)],
                "symbol": [symbols[0]],
                "open": [101.0],
                "high": [102.0],
                "low": [100.0],
                "close": [101.0],
                "adj_close": [101.0],
                "volume": [1_000_000],
            }
        )

    monkeypatch.setattr(daily_update_module, "get_session", get_session_override)
    monkeypatch.setattr(daily_update_module, "init_db", lambda: None)
    monkeypatch.setattr(daily_update_module, "_load_symbol_universe", lambda _session: ["SPY"])
    monkeypatch.setattr(daily_update_module, "fetch_prices_for_universe", fetch_override)

    daily_update_module.run_daily_update()

    assert fetch_called is False


def test_run_daily_update_skips_non_core_symbol_fetch_failure(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    start_date = datetime.date.today() - datetime.timedelta(days=2)

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

    def fetch_override(symbols, start=None, end=None):  # noqa: ARG001
        symbol = symbols[0]
        if symbol == "CUSTOM":
            raise RuntimeError("No market data returned for CUSTOM in production mode.")
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(start_date)],
                "symbol": [symbol],
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
    monkeypatch.setattr(
        daily_update_module,
        "_load_symbol_universe",
        lambda _session: ["QQQ", "CUSTOM"],
    )
    monkeypatch.setattr(daily_update_module, "fetch_prices_for_universe", fetch_override)

    daily_update_module.run_daily_update()


def test_run_daily_update_still_fails_for_core_symbol_fetch_failure(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    core_symbol = next(iter(PORTFOLIO_REQUIRED_SYMBOLS))

    def fetch_override(symbols, start=None, end=None):  # noqa: ARG001
        raise RuntimeError(f"No market data returned for {symbols[0]} in production mode.")

    monkeypatch.setattr(daily_update_module, "get_session", get_session_override)
    monkeypatch.setattr(daily_update_module, "init_db", lambda: None)
    monkeypatch.setattr(
        daily_update_module,
        "_load_symbol_universe",
        lambda _session: [core_symbol],
    )
    monkeypatch.setattr(daily_update_module, "fetch_prices_for_universe", fetch_override)

    with pytest.raises(RuntimeError):
        daily_update_module.run_daily_update()


def test_run_daily_update_skips_non_core_symbol_non_runtime_error(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    def fetch_override(symbols, start=None, end=None):  # noqa: ARG001
        if symbols[0] == "CUSTOM":
            raise ValueError("Unexpected upstream payload")
        return pd.DataFrame(
            {
                "date": [pd.Timestamp(datetime.date.today() - datetime.timedelta(days=1))],
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
    monkeypatch.setattr(
        daily_update_module,
        "_load_symbol_universe",
        lambda _session: ["QQQ", "CUSTOM"],
    )
    monkeypatch.setattr(daily_update_module, "fetch_prices_for_universe", fetch_override)

    daily_update_module.run_daily_update()
