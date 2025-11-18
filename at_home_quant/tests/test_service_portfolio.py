import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo
from at_home_quant.db.models import Base, PriceDaily, Ticker
from at_home_quant.portfolio.service import build_monthly_portfolio, compute_rebalance


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


def _seed_prices(session: Session, as_of_date: datetime.date) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_ids: dict[str, int] = {}
    benchmark_symbols = ["QQQ", "SPY", "VMID", "GLD", "BIL"]
    for symbol in benchmark_symbols:
        ticker_ids[symbol] = _add_ticker(session, BENCHMARKS[symbol])
    for info in SAMPLE_NASDAQ100.values():
        ticker_ids[info.symbol] = _add_ticker(session, info)

    dates = pd.bdate_range(end=as_of_date, periods=400)
    slopes = {
        "QQQ": 0.2,
        "SPY": 0.1,
        "VMID": 0.05,
        "GLD": 0.03,
        "BIL": 0.0,
        "AAPL": 0.25,
        "MSFT": 0.22,
    }
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes.get(symbol, 0.1)
        for i, dt in enumerate(dates):
            price = 100 + i * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    adj_close=price,
                )
            )
    session.commit()


def test_end_to_end_portfolio_and_rebalance():
    as_of_first = datetime.date(2024, 12, 31)
    as_of_second = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, as_of_second)
        portfolio = build_monthly_portfolio(as_of_first, session=session)
        assert abs(sum(p.weight for p in portfolio.positions) - 1.0) < 1e-6
        assert portfolio.universe_name == "NASDAQ100"

        instructions = compute_rebalance(as_of_second, session=session)
        assert instructions
        assert all(instr.action in {"buy", "sell", "hold"} for instr in instructions)
