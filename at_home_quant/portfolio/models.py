from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import date
from typing import List


@dataclass
class TargetPosition:
    ticker: str
    weight: float
    asset_type: str  # "equity", "gold", "cash"


@dataclass
class RiskConstraintViolation:
    code: str
    message: str
    current_value: float | None = None
    limit_value: float | None = None


@dataclass
class PortfolioRiskReport:
    as_of_date: date
    max_position_weight: float
    max_sector_weight: float
    max_region_weight: float
    turnover: float
    min_adv_usd_in_portfolio: float | None
    violations: List[RiskConstraintViolation] = field(default_factory=list)

    @property
    def is_within_limits(self) -> bool:
        return not self.violations


@dataclass
class TargetPortfolio:
    as_of_date: date
    positions: List[TargetPosition]
    universe_name: str
    equity_exposure: float
    defensive_exposure: float
    risk_report: PortfolioRiskReport | None = None

    def validate(self, tolerance: float = 1e-6) -> None:
        total_weight = sum(p.weight for p in self.positions)
        if abs(total_weight - 1.0) > tolerance:
            raise ValueError(f"Portfolio weights must sum to 1.0 (got {total_weight})")


@dataclass
class RebalanceInstruction:
    ticker: str
    action: str  # "buy", "sell", "hold"
    current_weight: float
    target_weight: float
    delta: float
    policy_status: str = "pass"
    policy_reason: str | None = None


__all__ = [
    "TargetPosition",
    "RiskConstraintViolation",
    "PortfolioRiskReport",
    "TargetPortfolio",
    "RebalanceInstruction",
]
