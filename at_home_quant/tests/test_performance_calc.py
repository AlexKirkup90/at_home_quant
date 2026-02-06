import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerInfo, TickerType, Universe
from at_home_quant.db.models import Base, PortfolioSnapshot, PriceDaily, Ticker
from at_home_quant.performance.calc import (
    compute_benchmark_return_for_period,
    compute_monthly_performance_series,
    compute_portfolio_turnover,
    compute_portfolio_return_for_period,
)
from at_home_quant.portfolio.models import TargetPortfolio, TargetPosition
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


def _add_price(session: Session, ticker_id: int, dt: datetime.date, price: float) -> None:
    session.add(PriceDaily(ticker_id=ticker_id, date=dt, adj_close=price))


def test_compute_portfolio_return_simple(session: Session):
    symbol = "AAA"
    info = TickerInfo(symbol, "Test", TickerType.EQUITY, Universe.NASDAQ100)
    tid = _add_ticker(session, info)
    start = datetime.date(2025, 1, 31)
    end = datetime.date(2025, 2, 28)
    _add_price(session, tid, start, 100)
    _add_price(session, tid, end, 101)
    session.commit()

    portfolio = TargetPortfolio(
        as_of_date=start,
        positions=[TargetPosition(symbol, 1.0, "equity")],
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
    )

    result = compute_portfolio_return_for_period(start, end, portfolio, session)
    assert pytest.approx(result) == 0.01


def test_compute_portfolio_turnover_simple():
    previous = TargetPortfolio(
        as_of_date=datetime.date(2025, 1, 31),
        positions=[TargetPosition("AAA", 1.0, "equity")],
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
    )
    nxt = TargetPortfolio(
        as_of_date=datetime.date(2025, 2, 28),
        positions=[TargetPosition("BBB", 1.0, "equity")],
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
    )
    turnover = compute_portfolio_turnover(previous, nxt)
    assert pytest.approx(turnover) == 1.0


def test_benchmark_return_with_mock_regime(session: Session):
    info = TickerInfo("QQQ", "QQQ", TickerType.ETF, Universe.NASDAQ100)
    tid = _add_ticker(session, info)
    start = datetime.date(2025, 1, 31)
    end = datetime.date(2025, 2, 28)
    _add_price(session, tid, start, 100)
    _add_price(session, tid, end, 104)
    session.commit()

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
    benchmark_name, benchmark_return = compute_benchmark_return_for_period(
        start, end, session, regime_getter=lambda *_args, **_kwargs: regime
    )
    assert benchmark_name == "QQQ"
    assert pytest.approx(benchmark_return) == 0.04


def test_benchmark_return_uses_period_start_when_requested(session: Session):
    info = TickerInfo("QQQ", "QQQ", TickerType.ETF, Universe.NASDAQ100)
    tid = _add_ticker(session, info)
    start = datetime.date(2025, 1, 31)
    end = datetime.date(2025, 2, 28)
    _add_price(session, tid, start, 100)
    _add_price(session, tid, end, 101)
    session.commit()

    called_dates = []
    score = UniverseScore(
        as_of_date=start,
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
    regime = RegimeDecision(
        as_of_date=start,
        best_universe="NASDAQ100",
        best_universe_score=1.0,
        all_universe_scores=[score],
    )

    def stub_regime(as_of_date, session=None):
        called_dates.append(as_of_date)
        return regime

    benchmark_name, benchmark_return = compute_benchmark_return_for_period(
        start,
        end,
        session,
        regime_getter=stub_regime,
        benchmark_timing="period_start",
    )
    assert called_dates == [start]
    assert benchmark_name == "QQQ"
    assert pytest.approx(benchmark_return) == 0.01


def test_compute_monthly_performance_series(session: Session):
    # set up benchmark ticker
    benchmark_info = TickerInfo("QQQ", "QQQ", TickerType.ETF, Universe.NASDAQ100)
    benchmark_id = _add_ticker(session, benchmark_info)
    # portfolio ticker
    equity_info = TickerInfo("AAA", "AAA", TickerType.EQUITY, Universe.NASDAQ100)
    equity_id = _add_ticker(session, equity_info)

    start = datetime.date(2025, 1, 31)
    end = datetime.date(2025, 2, 28)
    later = datetime.date(2025, 3, 31)

    _add_price(session, benchmark_id, start, 100)
    _add_price(session, benchmark_id, end, 102)
    _add_price(session, benchmark_id, later, 104)

    _add_price(session, equity_id, start, 50)
    _add_price(session, equity_id, end, 51)
    _add_price(session, equity_id, later, 52)

    session.commit()

    snapshot1 = PortfolioSnapshot(
        as_of_date=start,
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
        positions_json='[{"ticker": "AAA", "weight": 1.0, "asset_type": "equity"}]',
    )
    snapshot2 = PortfolioSnapshot(
        as_of_date=end,
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
        positions_json='[{"ticker": "AAA", "weight": 1.0, "asset_type": "equity"}]',
    )
    snapshot3 = PortfolioSnapshot(
        as_of_date=later,
        universe_name="NASDAQ100",
        equity_exposure=1.0,
        defensive_exposure=0.0,
        positions_json='[{"ticker": "AAA", "weight": 1.0, "asset_type": "equity"}]',
    )
    session.add_all([snapshot1, snapshot2, snapshot3])
    session.commit()

    score_start = UniverseScore(
        as_of_date=start,
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
    score_end = UniverseScore(
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
    regimes = {
        start: RegimeDecision(
            as_of_date=start,
            best_universe="NASDAQ100",
            best_universe_score=1.0,
            all_universe_scores=[score_start],
        ),
        end: RegimeDecision(
            as_of_date=end,
            best_universe="NASDAQ100",
            best_universe_score=1.0,
            all_universe_scores=[score_end],
        ),
    }

    def stub_regime(date_value, session=None):
        return regimes[date_value]

    performances = compute_monthly_performance_series(session=session, regime_getter=stub_regime)
    assert len(performances) == 2
    assert pytest.approx(performances[0].portfolio_return) == 0.02
    assert pytest.approx(performances[0].portfolio_return_gross) == 0.02
    assert pytest.approx(performances[0].transaction_cost) == 0.0
    assert pytest.approx(performances[0].benchmark_return) == 0.02
    assert pytest.approx(performances[0].alpha) == 0.0
    assert performances[0].benchmark_name == "QQQ"
    assert pytest.approx(performances[1].portfolio_return) == 0.0196078431372549
    assert performances[0].benchmark_selection_date == start
    assert performances[1].benchmark_selection_date == end


def test_compute_monthly_performance_series_applies_turnover_costs(session: Session):
    benchmark_info = TickerInfo("QQQ", "QQQ", TickerType.ETF, Universe.NASDAQ100)
    benchmark_id = _add_ticker(session, benchmark_info)
    aaa_info = TickerInfo("AAA", "AAA", TickerType.EQUITY, Universe.NASDAQ100)
    aaa_id = _add_ticker(session, aaa_info)
    bbb_info = TickerInfo("BBB", "BBB", TickerType.EQUITY, Universe.NASDAQ100)
    bbb_id = _add_ticker(session, bbb_info)

    start = datetime.date(2025, 1, 31)
    end = datetime.date(2025, 2, 28)
    _add_price(session, benchmark_id, start, 100)
    _add_price(session, benchmark_id, end, 101)
    _add_price(session, aaa_id, start, 100)
    _add_price(session, aaa_id, end, 102)
    _add_price(session, bbb_id, start, 100)
    _add_price(session, bbb_id, end, 100)
    session.commit()

    session.add_all(
        [
            PortfolioSnapshot(
                as_of_date=start,
                universe_name="NASDAQ100",
                equity_exposure=1.0,
                defensive_exposure=0.0,
                positions_json='[{"ticker": "AAA", "weight": 1.0, "asset_type": "equity"}]',
            ),
            PortfolioSnapshot(
                as_of_date=end,
                universe_name="NASDAQ100",
                equity_exposure=1.0,
                defensive_exposure=0.0,
                positions_json='[{"ticker": "BBB", "weight": 1.0, "asset_type": "equity"}]',
            ),
        ]
    )
    session.commit()

    score = UniverseScore(
        as_of_date=start,
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
    regime = RegimeDecision(as_of_date=start, best_universe="NASDAQ100", best_universe_score=1.0, all_universe_scores=[score])

    performances = compute_monthly_performance_series(
        session=session,
        regime_getter=lambda *_args, **_kwargs: regime,
        transaction_cost_bps=10.0,
        slippage_bps=10.0,
        benchmark_timing="period_start",
    )
    assert len(performances) == 1
    item = performances[0]
    assert pytest.approx(item.portfolio_return_gross) == 0.02
    assert pytest.approx(item.portfolio_turnover) == 1.0
    assert pytest.approx(item.transaction_cost) == 0.002
    assert pytest.approx(item.portfolio_return) == 0.018
