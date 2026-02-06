from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from at_home_quant.performance.models import MonthlyPerformance, PerformanceSummary


@dataclass(frozen=True)
class WalkForwardConfig:
    start_date: date
    end_date: date
    top_n: int
    benchmark_timing: str
    transaction_cost_bps: float
    slippage_bps: float
    max_position: float
    max_turnover: float


@dataclass
class WalkForwardRunResult:
    run_id: int
    created_at: datetime
    code_hash: str | None
    data_snapshot_hash: str
    config: WalkForwardConfig
    monthly: list[MonthlyPerformance]
    summary: PerformanceSummary


__all__ = ["WalkForwardConfig", "WalkForwardRunResult"]
