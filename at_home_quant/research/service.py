from __future__ import annotations

import datetime
from dataclasses import asdict
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from at_home_quant.backtest.service import run_walk_forward_backtest
from at_home_quant.db.models import DatasetSnapshot, PriceDaily, Ticker
from at_home_quant.db.session import get_session
from at_home_quant.performance.models import MonthlyPerformance
from at_home_quant.research.models import ExperimentWindow, ModelReport
from at_home_quant.research.registry import (
    complete_experiment,
    leakage_issues,
    register_experiment,
)


def _resolve_experiment_end_date(session: Session, end_date: datetime.date | None) -> datetime.date:
    if end_date is not None:
        return end_date
    resolved = session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()
    if resolved is None:
        raise ValueError("No price data available for walk-forward experiment.")
    return resolved


def _shift_months(base: datetime.date, months: int) -> datetime.date:
    return (pd.Timestamp(base) - pd.DateOffset(months=months)).date()


def _build_window(
    as_of_date: datetime.date,
    train_months: int,
    validation_months: int,
    holdout_months: int,
) -> ExperimentWindow:
    holdout_end = as_of_date
    holdout_start = _shift_months(holdout_end, holdout_months)
    validation_end = holdout_start - datetime.timedelta(days=1)
    validation_start = _shift_months(holdout_start, validation_months)
    train_end = validation_start - datetime.timedelta(days=1)
    train_start = _shift_months(validation_start, train_months)
    return ExperimentWindow(
        train_start=train_start,
        train_end=train_end,
        validation_start=validation_start,
        validation_end=validation_end,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
    )


def _feature_snapshot_hash(session: Session, as_of_date: datetime.date) -> str:
    row = session.execute(
        select(DatasetSnapshot)
        .where(
            DatasetSnapshot.layer == "feature",
            DatasetSnapshot.as_of_date <= as_of_date,
        )
        .order_by(DatasetSnapshot.as_of_date.desc(), DatasetSnapshot.created_at.desc())
    ).scalars().first()
    if row is None:
        raise ValueError("No feature snapshot found. Run backend pipeline first.")
    return row.snapshot_hash


def _period_return_for_symbol(
    session: Session,
    symbol: str,
    period_start: datetime.date,
    period_end: datetime.date,
) -> float | None:
    row = session.execute(
        select(Ticker.id).where(Ticker.symbol == symbol)
    ).scalar_one_or_none()
    if row is None:
        return None
    ticker_id = row
    start_price = session.execute(
        select(PriceDaily.adj_close)
        .where(
            PriceDaily.ticker_id == ticker_id,
            PriceDaily.date <= period_start,
        )
        .order_by(PriceDaily.date.desc())
    ).scalars().first()
    end_price = session.execute(
        select(PriceDaily.adj_close)
        .where(
            PriceDaily.ticker_id == ticker_id,
            PriceDaily.date <= period_end,
        )
        .order_by(PriceDaily.date.desc())
    ).scalars().first()
    if start_price is None or end_price is None or start_price == 0:
        return None
    return (end_price / start_price) - 1.0


def _challenger_comparison(
    session: Session,
    monthly: list[MonthlyPerformance],
) -> dict[str, Any]:
    if not monthly:
        return {"model_total_return": None, "benchmark_total_return": None, "challengers": {}}
    model_total = float((pd.Series([1 + item.portfolio_return for item in monthly]).prod()) - 1.0)
    benchmark_total = float((pd.Series([1 + item.benchmark_return for item in monthly]).prod()) - 1.0)
    period_start = monthly[0].period_start
    period_end = monthly[-1].period_end

    challengers: dict[str, float | None] = {}
    for symbol in ["SPY", "QQQ", "BIL"]:
        challengers[symbol] = _period_return_for_symbol(
            session=session,
            symbol=symbol,
            period_start=period_start,
            period_end=period_end,
        )
    return {
        "model_total_return": model_total,
        "benchmark_total_return": benchmark_total,
        "challengers": challengers,
        "outperformance_vs_spy": (
            None if challengers["SPY"] is None else model_total - float(challengers["SPY"])
        ),
        "outperformance_vs_qqq": (
            None if challengers["QQQ"] is None else model_total - float(challengers["QQQ"])
        ),
    }


def _robustness_checks(monthly: list[MonthlyPerformance]) -> dict[str, Any]:
    if not monthly:
        return {"regime_stability": {}, "turnover_adjusted_alpha_persistence": None}
    frame = pd.DataFrame(
        [
            {
                "benchmark_name": item.benchmark_name,
                "alpha": item.alpha,
                "turnover": item.portfolio_turnover,
            }
            for item in monthly
        ]
    )
    regime_stability = {}
    for benchmark_name, group in frame.groupby("benchmark_name"):
        regime_stability[str(benchmark_name)] = {
            "months": int(len(group)),
            "avg_alpha": float(group["alpha"].mean()),
            "alpha_std": float(group["alpha"].std(ddof=0)),
        }
    net_alpha = frame["alpha"] - (frame["turnover"].fillna(0.0) * 0.0005)
    persistence = None
    if len(net_alpha) > 1:
        prev = net_alpha.shift(1).dropna()
        curr = net_alpha.iloc[1:]
        persistence = float((prev.values * curr.values > 0).mean())
    return {
        "regime_stability": regime_stability,
        "turnover_adjusted_alpha_persistence": persistence,
    }


def run_walk_forward_experiment(
    end_date: datetime.date | None = None,
    top_n: int = 15,
    train_months: int = 36,
    validation_months: int = 12,
    holdout_months: int = 12,
    benchmark_timing: str | None = None,
    transaction_cost_bps: float | None = None,
    slippage_bps: float | None = None,
    max_position: float | None = None,
    max_turnover: float | None = None,
) -> ModelReport:
    with get_session() as session:
        as_of_date = _resolve_experiment_end_date(session, end_date=end_date)
        window = _build_window(
            as_of_date=as_of_date,
            train_months=train_months,
            validation_months=validation_months,
            holdout_months=holdout_months,
        )
        issues = leakage_issues(window)
        if issues:
            raise ValueError(f"Leakage checks failed: {'; '.join(issues)}")

        snapshot_hash = _feature_snapshot_hash(session, as_of_date=as_of_date)
        params = {
            "top_n": top_n,
            "train_months": train_months,
            "validation_months": validation_months,
            "holdout_months": holdout_months,
            "benchmark_timing": benchmark_timing,
            "transaction_cost_bps": transaction_cost_bps,
            "slippage_bps": slippage_bps,
            "max_position": max_position,
            "max_turnover": max_turnover,
        }
        experiment = register_experiment(
            session=session,
            run_type="walk_forward_experiment",
            as_of_date=as_of_date,
            feature_snapshot_hash=snapshot_hash,
            params=params,
            window=window,
        )
        try:
            run = run_walk_forward_backtest(
                start_date=window.holdout_start,
                end_date=window.holdout_end,
                top_n=top_n,
                benchmark_timing=benchmark_timing,
                transaction_cost_bps=transaction_cost_bps,
                slippage_bps=slippage_bps,
                max_position=max_position,
                max_turnover=max_turnover,
                experiment_id=experiment.id,
                session=session,
            )
            challenger = _challenger_comparison(session, run.monthly)
            robustness = _robustness_checks(run.monthly)
            metrics = asdict(run.summary)
            metrics["monthly_periods"] = len(run.monthly)
            complete_experiment(
                session=session,
                experiment_id=experiment.id,
                status="succeeded",
                metrics=metrics,
                challenger_comparison=challenger,
                robustness_checks=robustness,
            )
            return ModelReport(
                experiment_id=experiment.id,
                run_type=experiment.run_type,
                as_of_date=as_of_date,
                feature_snapshot_hash=snapshot_hash,
                metrics=metrics,
                challenger_comparison=challenger,
                robustness_checks=robustness,
                linked_run_id=run.run_id,
            )
        except Exception as exc:  # noqa: BLE001
            complete_experiment(
                session=session,
                experiment_id=experiment.id,
                status="failed",
                metrics={},
                challenger_comparison={},
                robustness_checks={},
                error_message=str(exc),
            )
            raise


__all__ = ["run_walk_forward_experiment"]
