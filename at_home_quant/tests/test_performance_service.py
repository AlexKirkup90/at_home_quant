import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerInfo, TickerType, Universe
from at_home_quant.db.models import Base, PortfolioSnapshot, PriceDaily, Ticker
from at_home_quant.performance.service import get_monthly_performance, get_performance_summary
from at_home_quant.regime.models import RegimeDecision, UniverseScore


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        yield session


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


def _seed_prices(session: Session, symbol: str, prices: list[tuple[datetime.date, float]]) -> None:
    ticker_info = TickerInfo(symbol, symbol, TickerType.ETF, Universe.NASDAQ100)
    ticker_id = _add_ticker(session, ticker_info)
    for dt, price in prices:
        session.add(PriceDaily(ticker_id=ticker_id, date=dt, adj_close=price))
    session.commit()
    return ticker_id


def _seed_snapshot(session: Session, as_of: datetime.date, ticker: str, weight: float = 1.0) -> None:
    import json

    snapshot = PortfolioSnapshot(
        as_of_date=as_of,
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
        positions_json=json.dumps(
            [{"ticker": ticker, "weight": weight, "asset_type": "equity"}]
        ),
    )
    session.add(snapshot)
    session.commit()


def test_get_monthly_performance_end_to_end(session: Session):
    start = datetime.date(2025, 1, 31)
    end = datetime.date(2025, 2, 28)

    _seed_prices(session, "AAA", [(start, 100.0), (end, 102.0)])
    _seed_prices(session, "QQQ", [(start, 100.0), (end, 101.0)])

    _seed_snapshot(session, start, "AAA")
    _seed_snapshot(session, end, "AAA")

    score = UniverseScore(
        as_of_date=end,
        universe_name="NASDAQ100",
        composite_score=1.0,
        trend=0,
        momentum_6m=0,
        momentum_12m=0,
        momentum_rank=1,
        realized_vol=0,
        drawdown=0,
        suggested_equity_min=0,
        suggested_equity_max=1,
    )
    regime = RegimeDecision(as_of_date=end, best_universe="NASDAQ100", best_universe_score=1.0, all_universe_scores=[score])

    def stub_regime(as_of_date, session=None):
        return regime

    performances = get_monthly_performance(session=session, regime_getter=stub_regime)
    assert len(performances) == 1
    assert pytest.approx(performances[0].portfolio_return) == 0.02
    assert pytest.approx(performances[0].benchmark_return) == 0.01
    assert performances[0].benchmark_name == "QQQ"

    summary = get_performance_summary(session=session, regime_getter=stub_regime)
    assert summary.total_alpha == pytest.approx(0.01)
    assert summary.avg_monthly_alpha == pytest.approx(0.01)
