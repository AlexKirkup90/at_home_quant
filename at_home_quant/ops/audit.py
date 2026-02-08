from __future__ import annotations

import datetime
import hashlib
import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.db.models import AuditEvent

LOGGER = logging.getLogger(__name__)


def _to_json(payload: dict[str, Any] | None) -> str:
    return json.dumps(payload or {}, sort_keys=True, default=str, separators=(",", ":"))


def _compute_hash(
    created_at: datetime.datetime,
    actor: str,
    environment: str,
    event_type: str,
    entity_type: str | None,
    entity_id: str | None,
    payload_json: str,
    prev_hash: str | None,
) -> str:
    raw = "|".join(
        [
            created_at.isoformat(),
            actor,
            environment,
            event_type,
            entity_type or "",
            entity_id or "",
            prev_hash or "",
            payload_json,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def append_audit_event(
    session: Session,
    event_type: str,
    payload: dict[str, Any] | None = None,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    actor: str | None = None,
    environment: str | None = None,
    fail_hard: bool = False,
) -> AuditEvent | None:
    settings = get_settings()
    resolved_actor = actor or settings.operator_id
    resolved_env = environment or settings.app_env
    created_at = datetime.datetime.utcnow()
    payload_json = _to_json(payload)

    try:
        prev = session.execute(
            select(AuditEvent.event_hash).order_by(AuditEvent.id.desc()).limit(1)
        ).scalar_one_or_none()
        event_hash = _compute_hash(
            created_at=created_at,
            actor=resolved_actor,
            environment=resolved_env,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            prev_hash=prev,
        )
        row = AuditEvent(
            created_at=created_at,
            actor=resolved_actor,
            environment=resolved_env,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            payload_json=payload_json,
            prev_hash=prev,
            event_hash=event_hash,
        )
        session.add(row)
        session.flush()
        return row
    except Exception as exc:  # noqa: BLE001
        if fail_hard:
            raise
        LOGGER.warning("Audit event failed (%s): %s", event_type, exc)
        return None


__all__ = ["append_audit_event"]
