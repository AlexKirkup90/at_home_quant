from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List


@dataclass
class TargetPosition:
    ticker: str
    weight: float
    asset_type: str  # "equity", "gold", "cash"


@dataclass
class TargetPortfolio:
    as_of_date: date
    positions: List[TargetPosition]
    universe_name: str
    equity_exposure: float
    defensive_exposure: float

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


__all__ = ["TargetPosition", "TargetPortfolio", "RebalanceInstruction"]
