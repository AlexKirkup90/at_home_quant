from __future__ import annotations

import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.db.models import ExperimentRun, ModelRelease
from at_home_quant.db.session import get_session
from at_home_quant.ops.audit import append_audit_event
from at_home_quant.ops.rbac import enforce_permission


def _require_release(session: Session, release_id: int) -> ModelRelease:
    row = session.execute(
        select(ModelRelease).where(ModelRelease.id == release_id)
    ).scalars().first()
    if row is None:
        raise ValueError(f"Model release {release_id} was not found.")
    return row


def _require_experiment(session: Session, experiment_id: int) -> ExperimentRun:
    row = session.execute(
        select(ExperimentRun).where(ExperimentRun.id == experiment_id)
    ).scalars().first()
    if row is None:
        raise ValueError(f"Experiment {experiment_id} was not found.")
    if row.status != "succeeded":
        raise ValueError(f"Experiment {experiment_id} is not succeeded (status={row.status}).")
    return row


def propose_model_release(
    *,
    model_name: str,
    environment: str,
    experiment_id: int,
    notes: str | None = None,
    actor: str | None = None,
    role: str | None = None,
    session: Session | None = None,
) -> ModelRelease:
    enforce_permission("release.propose", role=role)
    settings = get_settings()
    resolved_actor = actor or settings.operator_id

    def _propose(session_obj: Session) -> ModelRelease:
        _require_experiment(session_obj, experiment_id=experiment_id)
        now = datetime.datetime.utcnow()
        row = ModelRelease(
            model_name=model_name,
            environment=environment,
            experiment_id=experiment_id,
            status="proposed",
            proposed_by=resolved_actor,
            notes=notes,
            created_at=now,
            updated_at=now,
        )
        session_obj.add(row)
        session_obj.flush()
        append_audit_event(
            session_obj,
            event_type="model_release_proposed",
            entity_type="model_release",
            entity_id=str(row.id),
            payload={
                "model_name": model_name,
                "environment": environment,
                "experiment_id": experiment_id,
            },
        )
        return row

    if session is not None:
        return _propose(session)
    with get_session() as session_obj:
        return _propose(session_obj)


def approve_model_release(
    release_id: int,
    *,
    actor: str | None = None,
    role: str | None = None,
    session: Session | None = None,
) -> ModelRelease:
    enforce_permission("release.approve", role=role)
    settings = get_settings()
    resolved_actor = actor or settings.operator_id

    def _approve(session_obj: Session) -> ModelRelease:
        row = _require_release(session_obj, release_id=release_id)
        if row.status != "proposed":
            raise ValueError(f"Model release {release_id} cannot be approved from status={row.status}.")
        now = datetime.datetime.utcnow()
        row.status = "approved"
        row.approved_by = resolved_actor
        row.approved_at = now
        row.updated_at = now
        append_audit_event(
            session_obj,
            event_type="model_release_approved",
            entity_type="model_release",
            entity_id=str(row.id),
            payload={"model_name": row.model_name, "environment": row.environment},
        )
        return row

    if session is not None:
        return _approve(session)
    with get_session() as session_obj:
        return _approve(session_obj)


def activate_model_release(
    release_id: int,
    *,
    actor: str | None = None,
    role: str | None = None,
    session: Session | None = None,
) -> ModelRelease:
    enforce_permission("release.activate", role=role)
    settings = get_settings()
    resolved_actor = actor or settings.operator_id

    def _activate(session_obj: Session) -> ModelRelease:
        row = _require_release(session_obj, release_id=release_id)
        if settings.require_release_approval_stage_prod and row.environment in {"stage", "prod"}:
            if row.status != "approved":
                raise ValueError(
                    f"Model release {release_id} must be approved before {row.environment} activation."
                )
        elif row.status not in {"proposed", "approved", "deprecated", "rolled_back"}:
            raise ValueError(f"Model release {release_id} cannot be activated from status={row.status}.")

        now = datetime.datetime.utcnow()
        current_active = session_obj.execute(
            select(ModelRelease).where(
                ModelRelease.model_name == row.model_name,
                ModelRelease.environment == row.environment,
                ModelRelease.status == "active",
                ModelRelease.id != row.id,
            )
        ).scalars().all()
        for active in current_active:
            active.status = "deprecated"
            active.deactivated_at = now
            active.updated_at = now

        row.status = "active"
        row.activated_at = now
        row.updated_at = now
        append_audit_event(
            session_obj,
            event_type="model_release_activated",
            entity_type="model_release",
            entity_id=str(row.id),
            payload={
                "actor": resolved_actor,
                "model_name": row.model_name,
                "environment": row.environment,
                "experiment_id": row.experiment_id,
            },
        )
        return row

    if session is not None:
        return _activate(session)
    with get_session() as session_obj:
        return _activate(session_obj)


def rollback_model_release(
    model_name: str,
    environment: str,
    target_release_id: int,
    *,
    actor: str | None = None,
    role: str | None = None,
    session: Session | None = None,
) -> ModelRelease:
    enforce_permission("release.rollback", role=role)
    settings = get_settings()
    resolved_actor = actor or settings.operator_id

    def _rollback(session_obj: Session) -> ModelRelease:
        now = datetime.datetime.utcnow()
        target = _require_release(session_obj, target_release_id)
        if target.model_name != model_name or target.environment != environment:
            raise ValueError("Rollback target model/environment mismatch.")
        if target.status == "proposed":
            raise ValueError("Cannot rollback to a proposed (unapproved) release.")

        active_rows = session_obj.execute(
            select(ModelRelease).where(
                ModelRelease.model_name == model_name,
                ModelRelease.environment == environment,
                ModelRelease.status == "active",
            )
        ).scalars().all()
        for row in active_rows:
            if row.id != target.id:
                row.status = "rolled_back"
                row.deactivated_at = now
                row.updated_at = now
        target.status = "active"
        target.activated_at = now
        target.updated_at = now
        append_audit_event(
            session_obj,
            event_type="model_release_rollback",
            entity_type="model_release",
            entity_id=str(target.id),
            payload={
                "actor": resolved_actor,
                "model_name": model_name,
                "environment": environment,
            },
        )
        return target

    if session is not None:
        return _rollback(session)
    with get_session() as session_obj:
        return _rollback(session_obj)


def get_active_model_release(
    model_name: str,
    environment: str,
    *,
    session: Session | None = None,
) -> ModelRelease | None:
    def _get(session_obj: Session) -> ModelRelease | None:
        return session_obj.execute(
            select(ModelRelease).where(
                ModelRelease.model_name == model_name,
                ModelRelease.environment == environment,
                ModelRelease.status == "active",
            )
            .order_by(ModelRelease.activated_at.desc())
            .limit(1)
        ).scalars().first()

    if session is not None:
        return _get(session)
    with get_session() as session_obj:
        return _get(session_obj)


__all__ = [
    "propose_model_release",
    "approve_model_release",
    "activate_model_release",
    "rollback_model_release",
    "get_active_model_release",
]
