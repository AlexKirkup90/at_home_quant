from dataclasses import dataclass
from datetime import date
from typing import List


@dataclass
class TrendSignal:
    total_return_12m: float
    price_above_sma_10m: bool

    @property
    def is_bullish(self) -> bool:
        return self.total_return_12m > 0 and self.price_above_sma_10m


@dataclass
class UniverseScore:
    as_of_date: date
    universe_name: str
    composite_score: float
    trend: float
    momentum_6m: float
    momentum_12m: float
    momentum_rank: int
    realized_vol: float
    drawdown: float
    suggested_equity_min: float
    suggested_equity_max: float


@dataclass
class RegimeDecision:
    as_of_date: date
    best_universe: str
    best_universe_score: float
    all_universe_scores: List[UniverseScore]


__all__ = ["TrendSignal", "UniverseScore", "RegimeDecision"]
