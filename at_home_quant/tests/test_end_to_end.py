import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import (
    BENCHMARKS,
    SAMPLE_FTSE250,
    SAMPLE_NASDAQ100,
    SAMPLE_SP500,
    TickerInfo,
)
from at_home_quant.db.models import Base, PriceDaily, Ticker
from at_home_quant.portfolio.service import build_monthly_portfolio
from at_home_quant.regime.service import get_current_regime, get_universe_scores
from at_home_quant.selection.service import rank_universe


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
    tickers = (
        list(BENCHMARKS.values())
        + list(SAMPLE_NASDAQ100.values())
        + list(SAMPLE_SP500.values())
        + list(SAMPLE_FTSE250.values())
    )
    for info in tickers:
        ticker_ids[info.symbol] = _add_ticker(session, info)

    dates = pd.bdate_range(end=as_of_date, periods=400)
    slopes = {
        "QQQ": 0.2,
        "SPY": 0.1,
        "VMID": 0.05,
        "GLD": 0.02,
        "BIL": 0.0,
        "AAPL": 0.3,
        "MSFT": 0.25,
        "AMZN": 0.12,
        "GOOGL": 0.11,
        "TSCO.L": 0.08,
        "BVIC.L": 0.07,
    }
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes.get(symbol, 0.05)
        for idx, dt in enumerate(dates):
            price = 100 + idx * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    adj_close=price,
                )
            )
    session.commit()


@pytest.fixture()
def as_of_date() -> datetime.date:
    return datetime.date(2025, 1, 31)


@pytest.fixture()
def seeded_session(as_of_date: datetime.date) -> Session:
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, as_of_date)
        yield session


def test_end_to_end_regime_selection_portfolio(seeded_session: Session, as_of_date: datetime.date) -> None:
    scores = get_universe_scores(as_of_date, session=seeded_session)
    assert scores
    assert scores == get_universe_scores(as_of_date, session=seeded_session)

    regime = get_current_regime(as_of_date, session=seeded_session)
    assert regime.best_universe == "NASDAQ100"
    assert regime == get_current_regime(as_of_date, session=seeded_session)

    ranked = rank_universe(regime.best_universe, as_of_date, top_n=2, session=seeded_session)
    assert ranked
    assert ranked == rank_universe(regime.best_universe, as_of_date, top_n=2, session=seeded_session)
    assert ranked[0].composite_score >= ranked[1].composite_score

    portfolio = build_monthly_portfolio(as_of_date, top_n=2, session=seeded_session)
    assert portfolio.positions
    assert abs(sum(p.weight for p in portfolio.positions) - 1.0) < 1e-6
    assert portfolio == build_monthly_portfolio(as_of_date, top_n=2, session=seeded_session)
