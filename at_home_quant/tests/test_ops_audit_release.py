import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.db.models import AuditEvent, Base, DatasetSnapshot, ExperimentRun
from at_home_quant.ops.audit import append_audit_event
from at_home_quant.ops.release import (
    activate_model_release,
    approve_model_release,
    get_active_model_release,
    propose_model_release,
    rollback_model_release,
)


def _seed_succeeded_experiment(session: Session, as_of_date: datetime.date, snapshot_hash: str, run_type: str) -> int:
    session.add(
        DatasetSnapshot(
            layer="feature",
            as_of_date=as_of_date,
            snapshot_hash=snapshot_hash,
            row_count=1,
            run_id=None,
        )
    )
    session.flush()
    row = ExperimentRun(
        run_type=run_type,
        status="succeeded",
        as_of_date=as_of_date,
        feature_snapshot_hash=snapshot_hash,
        params_json="{}",
        metrics_json="{}",
        challenger_json="{}",
        robustness_json="{}",
    )
    session.add(row)
    session.flush()
    return row.id


def test_audit_events_form_hash_chain():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        first = append_audit_event(session, event_type="evt.a", payload={"a": 1}, fail_hard=True)
        second = append_audit_event(session, event_type="evt.b", payload={"b": 2}, fail_hard=True)
        assert first is not None
        assert second is not None
        assert second.prev_hash == first.event_hash
        assert first.event_hash != second.event_hash
        assert session.query(AuditEvent).count() == 2


def test_model_release_requires_approval_for_stage_activation():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2026, 2, 6)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        experiment_id = _seed_succeeded_experiment(
            session=session,
            as_of_date=as_of,
            snapshot_hash="f" * 64,
            run_type="backend_weekly",
        )
        proposed = propose_model_release(
            model_name="weekly_quant_v1",
            environment="stage",
            experiment_id=experiment_id,
            session=session,
        )
        try:
            activate_model_release(proposed.id, session=session)
            assert False, "Expected stage activation to require approval."
        except ValueError as exc:
            assert "must be approved" in str(exc)
        approve_model_release(proposed.id, session=session)
        active = activate_model_release(proposed.id, session=session)
        assert active.status == "active"
        assert get_active_model_release("weekly_quant_v1", "stage", session=session) is not None


def test_model_release_rollback_restores_target():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2026, 2, 6)
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        exp1 = _seed_succeeded_experiment(
            session=session,
            as_of_date=as_of,
            snapshot_hash="a" * 64,
            run_type="backend_weekly",
        )
        exp2 = _seed_succeeded_experiment(
            session=session,
            as_of_date=as_of,
            snapshot_hash="b" * 64,
            run_type="backend_weekly",
        )
        rel1 = propose_model_release(
            model_name="weekly_quant_v1",
            environment="dev",
            experiment_id=exp1,
            session=session,
        )
        rel2 = propose_model_release(
            model_name="weekly_quant_v1",
            environment="dev",
            experiment_id=exp2,
            session=session,
        )
        activate_model_release(rel1.id, session=session)
        activate_model_release(rel2.id, session=session)
        rolled = rollback_model_release(
            model_name="weekly_quant_v1",
            environment="dev",
            target_release_id=rel1.id,
            session=session,
        )
        assert rolled.id == rel1.id
        active = get_active_model_release("weekly_quant_v1", "dev", session=session)
        assert active is not None
        assert active.id == rel1.id
