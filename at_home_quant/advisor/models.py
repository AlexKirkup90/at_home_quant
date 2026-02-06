from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from at_home_quant.portfolio.models import TargetPortfolio, TargetPosition

DecisionAction = Literal["follow", "ignore", "partial"]
RecommendationAction = Literal["buy", "sell", "hold"]


@dataclass
class AdvisorRecommendationItem:
    id: int
    ticker: str
    recommendation: RecommendationAction
    current_weight: float
    target_weight: float
    delta: float
    rationale: str
    decision: DecisionAction | None = None
    executed_weight: float | None = None
    note: str | None = None


@dataclass
class AdvisorWatchItem:
    ticker: str
    composite_score: float
    reason: str


@dataclass
class WeeklyAdvisorReport:
    batch_id: int
    created_at: datetime
    as_of_date: date
    best_universe: str
    best_universe_score: float
    current_portfolio: TargetPortfolio
    target_portfolio: TargetPortfolio
    recommendations: list[AdvisorRecommendationItem]
    watchlist: list[AdvisorWatchItem]


@dataclass
class WorkflowDecisionInput:
    item_id: int
    decision: DecisionAction
    executed_weight: float | None = None
    note: str | None = None


@dataclass
class ExecutedPortfolioFromDecisions:
    as_of_date: date
    positions: list[TargetPosition]
    followed_items: int
    ignored_items: int
    partial_items: int


__all__ = [
    "AdvisorRecommendationItem",
    "AdvisorWatchItem",
    "WeeklyAdvisorReport",
    "WorkflowDecisionInput",
    "ExecutedPortfolioFromDecisions",
]
