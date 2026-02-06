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
    experiment_id: int | None = None


@dataclass
class WeeklyOutcomeItem:
    ticker: str
    recommendation: RecommendationAction
    decision: DecisionAction | None
    current_weight: float
    target_weight: float
    effective_weight: float
    forward_return: float
    model_impact: float
    decision_impact: float
    impact_gap: float


@dataclass
class WeeklyOutcomeReport:
    batch_id: int
    as_of_date: date
    evaluation_date: date
    horizon_days: int
    items: list[WeeklyOutcomeItem]
    model_active_return: float
    decision_active_return: float
    decision_alpha: float
    follow_hit_rate: float | None
    ignored_positive_count: int


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
    "WeeklyOutcomeItem",
    "WeeklyOutcomeReport",
    "WorkflowDecisionInput",
    "ExecutedPortfolioFromDecisions",
]
