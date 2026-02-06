from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class BackendPipelineResult:
    run_id: int
    status: str
    as_of_date: date
    data_snapshot_hash: str | None
    quality_summary: str
    recommendation_batch_id: int | None = None
    experiment_id: int | None = None


__all__ = ["BackendPipelineResult"]
