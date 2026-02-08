from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class DiscoveryCandidateItem:
    ticker: str
    source_universe: str
    composite_score: float
    discovery_score: float
    tier: str
    rationale: str
    risk_flags: list[str]
    score_delta: float | None
    is_current_holding: bool


@dataclass
class DiscoveryRunReport:
    run_id: int
    created_at: datetime
    as_of_date: date
    status: str
    best_universe: str | None
    data_snapshot_hash: str | None
    experiment_id: int | None
    candidate_count: int
    summary: dict
    error_message: str | None
    candidates: list[DiscoveryCandidateItem]


__all__ = ["DiscoveryCandidateItem", "DiscoveryRunReport"]
