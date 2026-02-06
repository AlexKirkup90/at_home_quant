from __future__ import annotations

import datetime
import json
from typing import Iterable, List, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.data.tickers import UNIVERSE_BENCHMARK_SYMBOL, Universe
from at_home_quant.db.models import AdvisorPortfolioSnapshot, PortfolioSnapshot, PriceDaily, Ticker
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
    benchmark_timing: str = "period_start",
) -> Tuple[str, float]:
    if benchmark_timing not in {"period_start", "period_end"}:
        raise ValueError(f"Unsupported benchmark_timing: {benchmark_timing}")
    decision_date = start_date if benchmark_timing == "period_start" else end_date
    decision = regime_getter(decision_date, session=session)
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
    positions = _deserialize_positions(json.loads(snapshot.positions_json))
    return TargetPortfolio(
        as_of_date=snapshot.as_of_date,
        positions=positions,
        universe_name=snapshot.universe_name,
        equity_exposure=snapshot.equity_exposure,
        defensive_exposure=snapshot.defensive_exposure,
    )


def _advisor_snapshot_to_portfolio(snapshot: AdvisorPortfolioSnapshot) -> TargetPortfolio:
    positions = _deserialize_positions(json.loads(snapshot.positions_json))
    return TargetPortfolio(
        as_of_date=snapshot.as_of_date,
        positions=positions,
        universe_name=snapshot.universe_name,
        equity_exposure=snapshot.equity_exposure,
        defensive_exposure=snapshot.defensive_exposure,
    )


def compute_portfolio_turnover(
    previous_portfolio: TargetPortfolio,
    next_portfolio: TargetPortfolio,
) -> float:
    previous = {position.ticker: position.weight for position in previous_portfolio.positions}
    nxt = {position.ticker: position.weight for position in next_portfolio.positions}
    tickers = set(previous) | set(nxt)
    gross_weight_change = sum(abs(nxt.get(ticker, 0.0) - previous.get(ticker, 0.0)) for ticker in tickers)
    return 0.5 * gross_weight_change


def compute_monthly_performance_series(
    session: Session | None = None,
    regime_getter=get_current_regime,
    benchmark_timing: str | None = None,
    transaction_cost_bps: float | None = None,
    slippage_bps: float | None = None,
) -> List[MonthlyPerformance]:
    settings = get_settings()
    resolved_benchmark_timing = benchmark_timing or settings.benchmark_selection_timing
    resolved_transaction_cost_bps = settings.transaction_cost_bps if transaction_cost_bps is None else transaction_cost_bps
    resolved_slippage_bps = settings.slippage_bps if slippage_bps is None else slippage_bps

    def _compute(session_obj: Session) -> List[MonthlyPerformance]:
        advisor_rows: Iterable[AdvisorPortfolioSnapshot] = session_obj.execute(
            select(AdvisorPortfolioSnapshot)
            .where(AdvisorPortfolioSnapshot.snapshot_type == "executed")
            .order_by(AdvisorPortfolioSnapshot.as_of_date, AdvisorPortfolioSnapshot.created_at)
        ).scalars()
        latest_by_date: dict[datetime.date, AdvisorPortfolioSnapshot] = {}
        for row in advisor_rows:
            latest_by_date[row.as_of_date] = row

        snapshots_list: list[TargetPortfolio]
        if len(latest_by_date) >= 2:
            snapshots_list = [
                _advisor_snapshot_to_portfolio(latest_by_date[as_of_date])
                for as_of_date in sorted(latest_by_date)
            ]
        else:
            snapshots: Iterable[PortfolioSnapshot] = session_obj.execute(
                select(PortfolioSnapshot).order_by(PortfolioSnapshot.as_of_date)
            ).scalars()
            snapshots_list = [_snapshot_to_portfolio(row) for row in snapshots]

        performances: List[MonthlyPerformance] = []
        for start_portfolio, end_portfolio in zip(snapshots_list, snapshots_list[1:]):
            gross_portfolio_return = compute_portfolio_return_for_period(
                start_portfolio.as_of_date,
                end_portfolio.as_of_date,
                start_portfolio,
                session_obj,
            )
            turnover = compute_portfolio_turnover(start_portfolio, end_portfolio)
            total_cost_bps = resolved_transaction_cost_bps + resolved_slippage_bps
            transaction_cost = turnover * (total_cost_bps / 10_000)
            net_portfolio_return = gross_portfolio_return - transaction_cost
            benchmark_name, benchmark_return = compute_benchmark_return_for_period(
                start_portfolio.as_of_date,
                end_portfolio.as_of_date,
                session_obj,
                regime_getter=regime_getter,
                benchmark_timing=resolved_benchmark_timing,
            )
            benchmark_selection_date = (
                start_portfolio.as_of_date
                if resolved_benchmark_timing == "period_start"
                else end_portfolio.as_of_date
            )
            performances.append(
                MonthlyPerformance(
                    period_start=start_portfolio.as_of_date,
                    period_end=end_portfolio.as_of_date,
                    portfolio_return=net_portfolio_return,
                    benchmark_name=benchmark_name,
                    benchmark_return=benchmark_return,
                    alpha=net_portfolio_return - benchmark_return,
                    portfolio_return_gross=gross_portfolio_return,
                    transaction_cost=transaction_cost,
                    portfolio_turnover=turnover,
                    benchmark_timing=resolved_benchmark_timing,
                    benchmark_selection_date=benchmark_selection_date,
                )
            )
        return performances

    if session is not None:
        return _compute(session)

    with get_session() as session_obj:
        return _compute(session_obj)


__all__ = [
    "compute_portfolio_return_for_period",
    "compute_portfolio_turnover",
    "compute_benchmark_return_for_period",
    "compute_monthly_performance_series",
]
