import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.db.models import Base, ExperimentRun
from at_home_quant.ops.gates import assert_production_run_controls, record_release_gate_run
from at_home_quant.ops.release import activate_model_release, approve_model_release, propose_model_release


def _seed_succeeded_experiment(session: Session, experiment_id: int = 1) -> int:
    row = ExperimentRun(
        id=experiment_id,
        run_type="backend_weekly",
        status="succeeded",
        as_of_date=datetime.date(2026, 2, 6),
        feature_snapshot_hash="f" * 64,
        params_json="{}",
        metrics_json="{}",
        challenger_json="{}",
        robustness_json="{}",
    )
    session.add(row)
    session.flush()
    return row.id


def test_production_controls_require_passed_gate_artifact(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("REQUIRE_PROD_RELEASE_CONTROLS", "true")
    monkeypatch.setenv("REQUIRE_GATE_CODE_HASH_MATCH", "false")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        _seed_succeeded_experiment(session, experiment_id=1)
        rel = propose_model_release(
            model_name="weekly_quant_v1",
            environment="prod",
            experiment_id=1,
            session=session,
        )
        approve_model_release(rel.id, session=session)
        activate_model_release(rel.id, session=session)
        with pytest.raises(ValueError, match="no passed gate artifact"):
            assert_production_run_controls(session, environment="prod")


def test_production_controls_pass_with_gate_and_active_release(monkeypatch):
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("REQUIRE_PROD_RELEASE_CONTROLS", "true")
    monkeypatch.setenv("REQUIRE_GATE_CODE_HASH_MATCH", "false")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        _seed_succeeded_experiment(session, experiment_id=2)
        rel = propose_model_release(
            model_name="weekly_quant_v1",
            environment="prod",
            experiment_id=2,
            session=session,
        )
        approve_model_release(rel.id, session=session)
        activate_model_release(rel.id, session=session)
        record_release_gate_run(
            session,
            environment="prod",
            gate_name="release_gates",
            status="passed",
            details={"results": [{"name": "unit", "ok": True}]},
            code_hash_value="abc123",
        )
        assert_production_run_controls(session, environment="prod")
