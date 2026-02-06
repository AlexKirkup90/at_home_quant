from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass(frozen=True)
class ExperimentWindow:
    train_start: date
    train_end: date
    validation_start: date
    validation_end: date
    holdout_start: date
    holdout_end: date


@dataclass
class ModelReport:
    experiment_id: int
    run_type: str
    as_of_date: date
    feature_snapshot_hash: str
    metrics: dict[str, Any]
    challenger_comparison: dict[str, Any]
    robustness_checks: dict[str, Any]
    artifact_path: str | None = None
    linked_run_id: int | None = None


__all__ = ["ExperimentWindow", "ModelReport"]
