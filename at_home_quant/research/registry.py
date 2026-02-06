from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.db.models import BacktestExperimentLink, ExperimentRun, WeeklyRecommendationExperimentLink
from at_home_quant.research.models import ExperimentWindow


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    return str(value)


def _to_json(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, default=_json_default, sort_keys=True)


def code_hash() -> str | None:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root)
            .decode("utf-8")
            .strip()
        )
    except Exception:  # noqa: BLE001
        return None


def leakage_issues(window: ExperimentWindow | None) -> list[str]:
    if window is None:
        return []
    checks = [
        ("train_start < train_end", window.train_start < window.train_end),
        ("train_end < validation_start", window.train_end < window.validation_start),
        ("validation_start < validation_end", window.validation_start < window.validation_end),
        ("validation_end < holdout_start", window.validation_end < window.holdout_start),
        ("holdout_start <= holdout_end", window.holdout_start <= window.holdout_end),
    ]
    return [label for label, passed in checks if not passed]


def register_experiment(
    session: Session,
    run_type: str,
    as_of_date: datetime.date,
    feature_snapshot_hash: str,
    params: dict[str, Any] | None = None,
    window: ExperimentWindow | None = None,
    artifact_path: str | None = None,
) -> ExperimentRun:
    issues = leakage_issues(window)
    row = ExperimentRun(
        run_type=run_type,
        status="running",
        as_of_date=as_of_date,
        feature_snapshot_hash=feature_snapshot_hash,
        params_json=_to_json(params),
        code_hash=code_hash(),
        train_start=(window.train_start if window else None),
        train_end=(window.train_end if window else None),
        validation_start=(window.validation_start if window else None),
        validation_end=(window.validation_end if window else None),
        holdout_start=(window.holdout_start if window else None),
        holdout_end=(window.holdout_end if window else None),
        leakage_checks_passed=0 if issues else 1,
        leakage_message=("; ".join(issues) if issues else None),
        artifact_path=artifact_path,
    )
    session.add(row)
    session.flush()
    return row


def complete_experiment(
    session: Session,
    experiment_id: int,
    status: str,
    metrics: dict[str, Any] | None = None,
    challenger_comparison: dict[str, Any] | None = None,
    robustness_checks: dict[str, Any] | None = None,
    artifact_path: str | None = None,
    error_message: str | None = None,
) -> None:
    row = session.execute(select(ExperimentRun).where(ExperimentRun.id == experiment_id)).scalars().first()
    if row is None:
        raise ValueError(f"Experiment {experiment_id} not found.")
    row.status = status
    row.metrics_json = _to_json(metrics)
    row.challenger_json = _to_json(challenger_comparison)
    row.robustness_json = _to_json(robustness_checks)
    if artifact_path is not None:
        row.artifact_path = artifact_path
    if error_message:
        row.leakage_message = error_message


def require_experiment(session: Session, experiment_id: int, run_type: str | None = None) -> ExperimentRun:
    row = session.execute(select(ExperimentRun).where(ExperimentRun.id == experiment_id)).scalars().first()
    if row is None:
        raise ValueError(f"Experiment {experiment_id} was not found.")
    if run_type is not None and row.run_type != run_type:
        raise ValueError(f"Experiment {experiment_id} has run_type={row.run_type}, expected {run_type}.")
    return row


def link_weekly_batch_to_experiment(session: Session, batch_id: int, experiment_id: int) -> None:
    existing = session.execute(
        select(WeeklyRecommendationExperimentLink).where(
            WeeklyRecommendationExperimentLink.batch_id == batch_id
        )
    ).scalars().first()
    if existing is not None:
        existing.experiment_id = experiment_id
        return
    session.add(WeeklyRecommendationExperimentLink(batch_id=batch_id, experiment_id=experiment_id))


def link_backtest_run_to_experiment(session: Session, backtest_run_id: int, experiment_id: int) -> None:
    existing = session.execute(
        select(BacktestExperimentLink).where(BacktestExperimentLink.backtest_run_id == backtest_run_id)
    ).scalars().first()
    if existing is not None:
        existing.experiment_id = experiment_id
        return
    session.add(BacktestExperimentLink(backtest_run_id=backtest_run_id, experiment_id=experiment_id))


def experiment_payload(row: ExperimentRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "run_type": row.run_type,
        "status": row.status,
        "as_of_date": row.as_of_date,
        "feature_snapshot_hash": row.feature_snapshot_hash,
        "params": json.loads(row.params_json or "{}"),
        "metrics": json.loads(row.metrics_json or "{}"),
        "challenger_comparison": json.loads(row.challenger_json or "{}"),
        "robustness_checks": json.loads(row.robustness_json or "{}"),
        "leakage_checks_passed": bool(row.leakage_checks_passed),
        "leakage_message": row.leakage_message,
    }


__all__ = [
    "code_hash",
    "register_experiment",
    "complete_experiment",
    "require_experiment",
    "leakage_issues",
    "link_weekly_batch_to_experiment",
    "link_backtest_run_to_experiment",
    "experiment_payload",
]
