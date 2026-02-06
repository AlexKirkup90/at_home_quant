from __future__ import annotations

import datetime
import hashlib
import json
import subprocess
from dataclasses import asdict
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from at_home_quant.backtest.models import WalkForwardConfig, WalkForwardRunResult
from at_home_quant.config.settings import get_settings
from at_home_quant.db.models import BacktestRun, Base, PriceDaily, Ticker
from at_home_quant.db.session import get_session
from at_home_quant.performance.calc import (
    compute_benchmark_return_for_period,
    compute_portfolio_return_for_period,
    compute_portfolio_turnover,
)
from at_home_quant.performance.models import MonthlyPerformance
from at_home_quant.performance.stats import compute_performance_summary
from at_home_quant.portfolio.service import build_monthly_portfolio
from at_home_quant.regime.service import get_current_regime


def _json_default(value):
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def _code_hash() -> str | None:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root)
            .decode("utf-8")
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def _data_snapshot_hash(session: Session, as_of_date: datetime.date) -> str:
    rows = session.execute(
        select(Ticker.symbol, func.count(PriceDaily.id), func.max(PriceDaily.date))
        .join(PriceDaily, PriceDaily.ticker_id == Ticker.id)
        .where(PriceDaily.date <= as_of_date)
        .group_by(Ticker.symbol)
        .order_by(Ticker.symbol)
    ).all()
    payload = "|".join(f"{symbol}:{count}:{latest_date}" for symbol, count, latest_date in rows)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _month_end_schedule(
    session: Session,
    start_date: datetime.date,
    end_date: datetime.date,
) -> list[datetime.date]:
    dates = session.execute(
        select(PriceDaily.date)
        .where(PriceDaily.date >= start_date, PriceDaily.date <= end_date)
        .distinct()
        .order_by(PriceDaily.date)
    ).scalars().all()
    if not dates:
        return []

    schedule: list[datetime.date] = []
    current_key: tuple[int, int] | None = None
    current_last: datetime.date | None = None
    for dt in dates:
        key = (dt.year, dt.month)
        if current_key is None:
            current_key = key
            current_last = dt
            continue
        if key != current_key:
            if current_last is not None:
                schedule.append(current_last)
            current_key = key
        current_last = dt
    if current_last is not None:
        schedule.append(current_last)
    return schedule


def _resolve_date_bounds(
    session: Session,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
) -> tuple[datetime.date, datetime.date]:
    min_date = session.execute(select(func.min(PriceDaily.date))).scalar_one_or_none()
    max_date = session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()
    if min_date is None or max_date is None:
        raise ValueError("No price history available for walk-forward backtest.")
    resolved_start = start_date or min_date
    resolved_end = end_date or max_date
    if resolved_start >= resolved_end:
        raise ValueError(
            f"Backtest start date must be before end date (got {resolved_start} >= {resolved_end})."
        )
    return resolved_start, resolved_end


def _run(
    session: Session,
    start_date: datetime.date | None,
    end_date: datetime.date | None,
    top_n: int,
    benchmark_timing: str,
    transaction_cost_bps: float,
    slippage_bps: float,
    max_position: float,
    max_turnover: float,
    regime_getter,
) -> WalkForwardRunResult:
    Base.metadata.create_all(bind=session.bind)
    resolved_start, resolved_end = _resolve_date_bounds(session, start_date=start_date, end_date=end_date)
    rebalance_dates = _month_end_schedule(session, start_date=resolved_start, end_date=resolved_end)
    if len(rebalance_dates) < 2:
        raise ValueError(
            "Need at least two monthly rebalance dates with market data for walk-forward backtest."
        )

    portfolios = []
    previous_portfolio = None
    for rebalance_date in rebalance_dates:
        portfolio = build_monthly_portfolio(
            as_of_date=rebalance_date,
            top_n=top_n,
            max_position=max_position,
            persist_snapshot=False,
            previous_portfolio=previous_portfolio,
            session=session,
        )
        portfolios.append(portfolio)
        previous_portfolio = portfolio

    monthly: list[MonthlyPerformance] = []
    for previous, current in zip(portfolios, portfolios[1:]):
        gross_return = compute_portfolio_return_for_period(
            previous.as_of_date,
            current.as_of_date,
            previous,
            session,
        )
        turnover = compute_portfolio_turnover(previous, current)
        total_cost_bps = transaction_cost_bps + slippage_bps
        transaction_cost = turnover * (total_cost_bps / 10_000)
        net_return = gross_return - transaction_cost
        benchmark_name, benchmark_return = compute_benchmark_return_for_period(
            previous.as_of_date,
            current.as_of_date,
            session,
            regime_getter=regime_getter,
            benchmark_timing=benchmark_timing,
        )
        benchmark_selection_date = (
            previous.as_of_date if benchmark_timing == "period_start" else current.as_of_date
        )
        monthly.append(
            MonthlyPerformance(
                period_start=previous.as_of_date,
                period_end=current.as_of_date,
                portfolio_return=net_return,
                benchmark_name=benchmark_name,
                benchmark_return=benchmark_return,
                alpha=net_return - benchmark_return,
                portfolio_return_gross=gross_return,
                transaction_cost=transaction_cost,
                portfolio_turnover=turnover,
                benchmark_timing=benchmark_timing,
                benchmark_selection_date=benchmark_selection_date,
            )
        )

    summary = compute_performance_summary(monthly)
    config = WalkForwardConfig(
        start_date=resolved_start,
        end_date=resolved_end,
        top_n=top_n,
        benchmark_timing=benchmark_timing,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
        max_position=max_position,
        max_turnover=max_turnover,
    )
    run = BacktestRun(
        start_date=resolved_start,
        end_date=resolved_end,
        code_hash=_code_hash(),
        data_snapshot_hash=_data_snapshot_hash(session, as_of_date=resolved_end),
        config_json=json.dumps(asdict(config), default=_json_default, sort_keys=True),
        summary_json=json.dumps(asdict(summary), default=_json_default, sort_keys=True),
        monthly_results_json=json.dumps([asdict(item) for item in monthly], default=_json_default, sort_keys=True),
    )
    session.add(run)
    session.flush()
    return WalkForwardRunResult(
        run_id=run.id,
        created_at=run.created_at,
        code_hash=run.code_hash,
        data_snapshot_hash=run.data_snapshot_hash,
        config=config,
        monthly=monthly,
        summary=summary,
    )


def run_walk_forward_backtest(
    start_date: datetime.date | None = None,
    end_date: datetime.date | None = None,
    top_n: int = 15,
    benchmark_timing: str | None = None,
    transaction_cost_bps: float | None = None,
    slippage_bps: float | None = None,
    max_position: float | None = None,
    max_turnover: float | None = None,
    regime_getter=get_current_regime,
    session: Session | None = None,
) -> WalkForwardRunResult:
    settings = get_settings()
    resolved_benchmark_timing = benchmark_timing or settings.benchmark_selection_timing
    resolved_transaction_cost_bps = (
        settings.transaction_cost_bps if transaction_cost_bps is None else transaction_cost_bps
    )
    resolved_slippage_bps = settings.slippage_bps if slippage_bps is None else slippage_bps
    resolved_max_position = settings.risk_max_position if max_position is None else max_position
    resolved_max_turnover = settings.risk_max_turnover if max_turnover is None else max_turnover

    if session is not None:
        return _run(
            session=session,
            start_date=start_date,
            end_date=end_date,
            top_n=top_n,
            benchmark_timing=resolved_benchmark_timing,
            transaction_cost_bps=resolved_transaction_cost_bps,
            slippage_bps=resolved_slippage_bps,
            max_position=resolved_max_position,
            max_turnover=resolved_max_turnover,
            regime_getter=regime_getter,
        )

    with get_session() as session_obj:
        return _run(
            session=session_obj,
            start_date=start_date,
            end_date=end_date,
            top_n=top_n,
            benchmark_timing=resolved_benchmark_timing,
            transaction_cost_bps=resolved_transaction_cost_bps,
            slippage_bps=resolved_slippage_bps,
            max_position=resolved_max_position,
            max_turnover=resolved_max_turnover,
            regime_getter=regime_getter,
        )


__all__ = ["run_walk_forward_backtest"]
