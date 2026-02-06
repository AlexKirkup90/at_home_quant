import datetime
import json

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.backtest.service import run_walk_forward_backtest
from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo
from at_home_quant.db.models import BacktestRun, Base, PriceDaily, Ticker


def _add_ticker(session: Session, info: TickerInfo) -> int:
    ticker = Ticker(
        symbol=info.symbol,
        name=info.name,
        asset_type=info.asset_type,
        universe=info.universe,
        currency=info.currency,
    )
    session.add(ticker)
    session.flush()
    return ticker.id


def _seed_prices(session: Session, as_of_date: datetime.date, periods: int = 500) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_ids: dict[str, int] = {}
    benchmark_symbols = ["QQQ", "SPY", "VMID", "GLD", "BIL"]
    for symbol in benchmark_symbols:
        ticker_ids[symbol] = _add_ticker(session, BENCHMARKS[symbol])
    for info in SAMPLE_NASDAQ100.values():
        ticker_ids[info.symbol] = _add_ticker(session, info)

    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {
        "QQQ": 0.20,
        "SPY": 0.12,
        "VMID": 0.06,
        "GLD": 0.03,
        "BIL": 0.00,
        "AAPL": 0.28,
        "MSFT": 0.24,
    }
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes.get(symbol, 0.1)
        for idx, dt in enumerate(dates):
            price = 100 + idx * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    adj_close=price,
                    volume=2_000_000,
                )
            )
    session.commit()


def test_run_walk_forward_backtest_persists_registry_artifacts():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, datetime.date(2025, 4, 30))
        result = run_walk_forward_backtest(
            start_date=datetime.date(2025, 1, 1),
            end_date=datetime.date(2025, 4, 30),
            top_n=2,
            session=session,
        )
        assert result.run_id > 0
        assert len(result.monthly) >= 2
        assert result.summary.months == len(result.monthly)
        row = session.query(BacktestRun).filter(BacktestRun.id == result.run_id).one()
        assert row.data_snapshot_hash
        assert len(row.data_snapshot_hash) == 64
        assert json.loads(row.config_json)["top_n"] == 2
        assert json.loads(row.summary_json)["months"] == len(result.monthly)
