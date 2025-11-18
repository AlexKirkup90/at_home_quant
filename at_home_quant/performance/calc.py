from __future__ import annotations

import datetime
from typing import Iterable, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import UNIVERSE_BENCHMARK_SYMBOL, Universe
from at_home_quant.db.models import PortfolioSnapshot, PriceDaily, Ticker
from at_home_quant.db.session import get_session
from at_home_quant.performance.models import MonthlyPerformance
from at_home_quant.portfolio.models import TargetPortfolio, TargetPosition
from at_home_quant.regime.service import get_current_regime


def _deserialize_positions(data: list[dict]) -> list[TargetPosition]:
    return [TargetPosition(**item) for item in data]


def _load_price_on_or_before(session: Session, symbol: str, as_of_date: datetime.date) -> float:
    row = (
        session.execute(
            select(PriceDaily.adj_close)
            .join(Ticker, Ticker.id == PriceDaily.ticker_id)
            .where(Ticker.symbol == symbol, PriceDaily.date <= as_of_date)
            .order_by(PriceDaily.date.desc())
        )
        .scalars()
        .first()
    )
    if row is None:
        raise ValueError(f"No price available for {symbol} on or before {as_of_date}")
    return float(row)


def compute_portfolio_return_for_period(
    start_date: datetime.date,
    end_date: datetime.date,
    portfolio_snapshot: TargetPortfolio,
    session: Session,
) -> float:
    returns: List[float] = []
    for position in portfolio_snapshot.positions:
        start_price = _load_price_on_or_before(session, position.ticker, start_date)
        end_price = _load_price_on_or_before(session, position.ticker, end_date)
        if start_price == 0:
            raise ValueError(f"Start price for {position.ticker} is zero")
        pct_return = (end_price / start_price) - 1.0
        returns.append(position.weight * pct_return)
    return sum(returns)


def compute_benchmark_return_for_period(
    start_date: datetime.date,
    end_date: datetime.date,
    session: Session,
    regime_getter=get_current_regime,
) -> Tuple[str, float]:
    decision = regime_getter(end_date, session=session)
    universe_key = decision.best_universe
    universe_enum = None
    if isinstance(universe_key, Universe):
        universe_enum = universe_key
    else:
        try:
            universe_enum = Universe[universe_key]
        except KeyError:
            try:
                universe_enum = Universe(universe_key)
            except Exception:
                universe_enum = None

    benchmark_symbol = UNIVERSE_BENCHMARK_SYMBOL.get(universe_enum)
    if benchmark_symbol is None:
        raise ValueError(f"No benchmark defined for universe {decision.best_universe}")
    start_price = _load_price_on_or_before(session, benchmark_symbol, start_date)
    end_price = _load_price_on_or_before(session, benchmark_symbol, end_date)
    benchmark_return = (end_price / start_price) - 1.0
    return benchmark_symbol, benchmark_return


def _snapshot_to_portfolio(snapshot: PortfolioSnapshot) -> TargetPortfolio:
    import json

    positions = _deserialize_positions(json.loads(snapshot.positions_json))
    return TargetPortfolio(
        as_of_date=snapshot.as_of_date,
        positions=positions,
        universe_name=snapshot.universe_name,
        equity_exposure=snapshot.equity_exposure,
        defensive_exposure=snapshot.defensive_exposure,
    )


def compute_monthly_performance_series(
    session: Session | None = None,
    regime_getter=get_current_regime,
) -> List[MonthlyPerformance]:
    def _compute(session_obj: Session) -> List[MonthlyPerformance]:
        snapshots: Iterable[PortfolioSnapshot] = session_obj.execute(
            select(PortfolioSnapshot).order_by(PortfolioSnapshot.as_of_date)
        ).scalars()
        snapshots_list = list(snapshots)
        performances: List[MonthlyPerformance] = []
        for prev, curr in zip(snapshots_list, snapshots_list[1:]):
            start_portfolio = _snapshot_to_portfolio(prev)
            portfolio_return = compute_portfolio_return_for_period(
                prev.as_of_date, curr.as_of_date, start_portfolio, session_obj
            )
            benchmark_name, benchmark_return = compute_benchmark_return_for_period(
                prev.as_of_date, curr.as_of_date, session_obj, regime_getter=regime_getter
            )
            performances.append(
                MonthlyPerformance(
                    period_start=prev.as_of_date,
                    period_end=curr.as_of_date,
                    portfolio_return=portfolio_return,
                    benchmark_name=benchmark_name,
                    benchmark_return=benchmark_return,
                    alpha=portfolio_return - benchmark_return,
                )
            )
        return performances

    if session is not None:
        return _compute(session)

    with get_session() as session_obj:
        return _compute(session_obj)


__all__ = [
    "compute_portfolio_return_for_period",
    "compute_benchmark_return_for_period",
    "compute_monthly_performance_series",
]
