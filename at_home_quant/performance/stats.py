from __future__ import annotations

import math
import statistics
from datetime import date
from typing import List, Optional

from at_home_quant.performance.models import MonthlyPerformance, PerformanceSummary


def _annualized_return(total_return: float, start: date, end: date) -> float:
    days = (end - start).days
    if days <= 0:
        return 0.0
    years = days / 365.25
    return (1 + total_return) ** (1 / years) - 1 if years > 0 else 0.0


def compute_performance_summary(series: List[MonthlyPerformance]) -> PerformanceSummary:
    if not series:
        raise ValueError("Monthly performance series is empty")
    start_date = series[0].period_start
    end_date = series[-1].period_end
    monthly_returns = [item.portfolio_return for item in series]
    monthly_returns_gross = [
        item.portfolio_return_gross if item.portfolio_return_gross is not None else item.portfolio_return
        for item in series
    ]
    months = len(monthly_returns)

    total_return = 1.0
    for r in monthly_returns:
        total_return *= 1 + r
    total_return -= 1
    gross_total_return = 1.0
    for r in monthly_returns_gross:
        gross_total_return *= 1 + r
    gross_total_return -= 1
    total_transaction_cost = sum(item.transaction_cost for item in series)

    cagr = _annualized_return(total_return, start_date, end_date)

    volatility: Optional[float]
    if months < 2:
        volatility = None
    else:
        vol_monthly = statistics.pstdev(monthly_returns)
        volatility = vol_monthly * math.sqrt(12)

    equity_curve = []
    cumulative = 1.0
    max_peak = 1.0
    max_drawdown = 0.0
    for r in monthly_returns:
        cumulative *= 1 + r
        equity_curve.append(cumulative)
        max_peak = max(max_peak, cumulative)
        drawdown = (cumulative - max_peak) / max_peak
        max_drawdown = min(max_drawdown, drawdown)

    sharpe: Optional[float]
    if volatility is None or volatility == 0:
        sharpe = None
    else:
        annual_return = (1 + total_return) ** (12 / months) - 1 if months else 0.0
        sharpe = annual_return / volatility

    alphas = [item.alpha for item in series]
    total_alpha = sum(alphas)
    avg_monthly_alpha = statistics.mean(alphas)
    positive_alpha_months = sum(1 for value in alphas if value > 0)
    alpha_hit_rate = positive_alpha_months / months if months else 0.0
    avg_monthly_turnover = statistics.mean(item.portfolio_turnover for item in series)

    tracking_error: Optional[float]
    information_ratio: Optional[float]
    if months < 2:
        tracking_error = None
        information_ratio = None
    else:
        monthly_tracking_error = statistics.pstdev(alphas)
        tracking_error = monthly_tracking_error * math.sqrt(12)
        if monthly_tracking_error == 0:
            information_ratio = None
        else:
            information_ratio = (avg_monthly_alpha * 12) / tracking_error

    return PerformanceSummary(
        start_date=start_date,
        end_date=end_date,
        total_return=total_return,
        gross_total_return=gross_total_return,
        total_transaction_cost=total_transaction_cost,
        cagr=cagr,
        volatility=volatility,
        max_drawdown=max_drawdown,
        sharpe=sharpe,
        tracking_error=tracking_error,
        information_ratio=information_ratio,
        total_alpha=total_alpha,
        avg_monthly_alpha=avg_monthly_alpha,
        positive_alpha_months=positive_alpha_months,
        alpha_hit_rate=alpha_hit_rate,
        avg_monthly_turnover=avg_monthly_turnover,
        months=months,
    )


__all__ = ["compute_performance_summary"]
