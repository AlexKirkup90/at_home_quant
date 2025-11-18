from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class MonthlyPerformance:
    period_start: date
    period_end: date
    portfolio_return: float
    benchmark_name: str
    benchmark_return: float
    alpha: float


@dataclass
class PerformanceSummary:
    start_date: date
    end_date: date
    total_return: float
    cagr: float
    volatility: Optional[float]
    max_drawdown: float
    sharpe: Optional[float]
    total_alpha: float
    avg_monthly_alpha: float
    months: int


__all__ = ["MonthlyPerformance", "PerformanceSummary"]
