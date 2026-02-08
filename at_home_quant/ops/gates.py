from __future__ import annotations

import datetime
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.db.models import ReleaseGateRun
from at_home_quant.ops.release import get_active_model_release
from at_home_quant.research.registry import code_hash


def _json(payload: dict | None) -> str:
    return json.dumps(payload or {}, sort_keys=True, default=str)


def record_release_gate_run(
    session: Session,
    *,
    environment: str,
    gate_name: str,
    status: str,
    details: dict | None = None,
    code_hash_value: str | None = None,
) -> ReleaseGateRun:
    row = ReleaseGateRun(
        environment=environment,
        gate_name=gate_name,
        status=status,
        code_hash=(code_hash_value if code_hash_value is not None else code_hash()),
        details_json=_json(details),
    )
    session.add(row)
    session.flush()
    return row


def latest_release_gate_run(
    session: Session,
    *,
    environment: str,
    gate_name: str,
    status: str | None = None,
) -> ReleaseGateRun | None:
    stmt = select(ReleaseGateRun).where(
        ReleaseGateRun.environment == environment,
        ReleaseGateRun.gate_name == gate_name,
    )
    if status is not None:
        stmt = stmt.where(ReleaseGateRun.status == status)
    return session.execute(stmt.order_by(ReleaseGateRun.created_at.desc()).limit(1)).scalars().first()


def assert_production_run_controls(
    session: Session,
    *,
    environment: str | None = None,
    gate_name: str = "release_gates",
) -> None:
    settings = get_settings()
    env_name = environment or settings.app_env
    if env_name != "prod":
        return
    if not settings.require_prod_release_controls:
        return

    gate = latest_release_gate_run(
        session,
        environment=env_name,
        gate_name=gate_name,
        status="passed",
    )
    if gate is None:
        raise ValueError(
            f"Production controls failed: no passed gate artifact for env={env_name} gate={gate_name}."
        )
    now = datetime.datetime.utcnow()
    age_hours = (now - gate.created_at).total_seconds() / 3600.0
    if age_hours > settings.prod_gate_max_age_hours:
        raise ValueError(
            f"Production controls failed: latest passed gate is stale ({age_hours:.2f}h > "
            f"{settings.prod_gate_max_age_hours}h)."
        )
    current_hash = code_hash()
    if settings.require_gate_code_hash_match and current_hash and gate.code_hash:
        if current_hash != gate.code_hash:
            raise ValueError(
                "Production controls failed: gate artifact code hash does not match current code hash."
            )

    release = get_active_model_release(
        settings.required_active_model_name,
        env_name,
        session=session,
    )
    if release is None:
        raise ValueError(
            f"Production controls failed: no active release for model='{settings.required_active_model_name}' "
            f"in env={env_name}."
        )


__all__ = [
    "record_release_gate_run",
    "latest_release_gate_run",
    "assert_production_run_controls",
]
