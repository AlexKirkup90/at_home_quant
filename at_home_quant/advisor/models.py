from __future__ import annotations

from dataclasses import dataclass, field
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
    pretrade_summary: dict = field(default_factory=dict)
    pretrade_checks: list[dict] = field(default_factory=list)


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
    model_portfolio_return: float | None = None
    decision_portfolio_return: float | None = None
    benchmark_return: float | None = None
    model_vs_benchmark: float | None = None
    decision_vs_benchmark: float | None = None
    model_implementation_shortfall: float = 0.0
    decision_implementation_shortfall: float = 0.0
    shortfall_gap: float = 0.0


@dataclass
class WeeklyOutcomeMetricPoint:
    batch_id: int
    as_of_date: date
    evaluation_date: date
    horizon_days: int
    item_count: int
    decision_alpha: float
    follow_hit_rate: float | None
    shortfall_gap: float
    decision_vs_benchmark: float | None
    model_active_return: float
    decision_active_return: float
    model_implementation_shortfall: float
    decision_implementation_shortfall: float


@dataclass
class WeeklyOutcomeTrendReport:
    horizon_days: int
    rolling_window: int
    points: list[WeeklyOutcomeMetricPoint]
    latest_rolling_decision_alpha: float | None
    latest_rolling_shortfall_gap: float | None
    flag_negative_decision_alpha_streak: bool
    flag_negative_rolling_decision_alpha: bool
    flag_rising_shortfall_gap: bool


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
    "WeeklyOutcomeMetricPoint",
    "WeeklyOutcomeTrendReport",
    "WorkflowDecisionInput",
    "ExecutedPortfolioFromDecisions",
]
