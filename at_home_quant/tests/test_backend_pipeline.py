import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import at_home_quant.backend.service as backend_service_module
from at_home_quant.data.tickers import BENCHMARKS, TickerInfo
from at_home_quant.db.models import BackendRun, Base, DataLayerPrice, DatasetSnapshot, PriceDaily, Ticker


def _add_ticker(session: Session, info: TickerInfo) -> int:
    ticker = Ticker(
        symbol=info.symbol,
        name=info.name,
        asset_type=info.asset_type,
        universe=info.universe,
        currency=info.currency,
    )
    session.add(ticker)
    session.flush()
    return ticker.id


def _seed_prices(session: Session, as_of_date: datetime.date, periods: int = 260) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_ids: dict[str, int] = {}
    for symbol in ["QQQ", "SPY", "VMID", "GLD", "BIL"]:
        ticker_ids[symbol] = _add_ticker(session, BENCHMARKS[symbol])
    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {"QQQ": 0.2, "SPY": 0.1, "VMID": 0.05, "GLD": 0.03, "BIL": 0.0}
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes[symbol]
        for idx, dt in enumerate(dates):
            price = 100 + idx * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    open=price,
                    high=price * 1.01,
                    low=price * 0.99,
                    close=price,
                    adj_close=price,
                    volume=1_000_000,
                    return_=0.0,
                )
            )
    session.commit()


def test_run_backend_pipeline_creates_layer_snapshots(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with Session(engine) as session:
        _seed_prices(session, datetime.date(2025, 2, 28))

    def session_override():
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        return _ctx()

    monkeypatch.setattr(backend_service_module, "get_session", session_override)
    monkeypatch.setattr(backend_service_module, "init_db", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_daily_update", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_fundamentals_update", lambda **_kwargs: 0)

    result = backend_service_module.run_backend_pipeline(
        as_of_date=datetime.date(2025, 2, 28),
        include_weekly_recommendation=False,
        retries=0,
    )
    assert result.status == "succeeded"
    assert result.data_snapshot_hash

    with Session(engine) as session:
        layers = session.query(DatasetSnapshot.layer).all()
        run = session.query(BackendRun).filter(BackendRun.id == result.run_id).one()
        assert sorted(layer for (layer,) in layers) == ["clean", "feature", "raw"]
        assert run.status == "succeeded"


def test_run_backend_pipeline_marks_failed_on_empty_data(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)

    def session_override():
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        return _ctx()

    monkeypatch.setattr(backend_service_module, "get_session", session_override)
    monkeypatch.setattr(backend_service_module, "init_db", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_daily_update", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_fundamentals_update", lambda **_kwargs: 0)

    try:
        backend_service_module.run_backend_pipeline(include_weekly_recommendation=False, retries=0)
        assert False, "Expected backend pipeline to fail with empty data."
    except Exception:
        pass

    with Session(engine) as session:
        run = session.query(BackendRun).order_by(BackendRun.id.desc()).first()
        assert run is not None
        assert run.status == "failed"


def test_run_backend_pipeline_persists_layer_prices_with_small_insert_batches(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with Session(engine) as session:
        _seed_prices(session, datetime.date(2025, 2, 28))

    def session_override():
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        return _ctx()

    monkeypatch.setattr(backend_service_module, "get_session", session_override)
    monkeypatch.setattr(backend_service_module, "init_db", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_daily_update", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_fundamentals_update", lambda **_kwargs: 0)
    monkeypatch.setattr(backend_service_module, "SQLITE_SAFE_INSERT_BATCH_SIZE", 1)

    result = backend_service_module.run_backend_pipeline(
        as_of_date=datetime.date(2025, 2, 28),
        include_weekly_recommendation=False,
        retries=0,
    )

    assert result.status == "succeeded"
    with Session(engine) as session:
        row_count = session.query(DataLayerPrice).count()
    assert row_count > 0


def test_run_backend_pipeline_preserves_root_error_when_experiment_finalize_fails(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    as_of_date = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of_date)

    def session_override():
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            with Session(engine) as session:
                try:
                    yield session
                    session.commit()
                except Exception:
                    session.rollback()
                    raise

        return _ctx()

    class _Experiment:
        id = 4

    monkeypatch.setattr(backend_service_module, "get_session", session_override)
    monkeypatch.setattr(backend_service_module, "init_db", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_daily_update", lambda: None)
    monkeypatch.setattr(backend_service_module, "run_fundamentals_update", lambda **_kwargs: 0)
    monkeypatch.setattr(backend_service_module, "register_experiment", lambda **_kwargs: _Experiment())
    monkeypatch.setattr(
        backend_service_module,
        "generate_weekly_recommendation",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("root cause failure")),
    )
    monkeypatch.setattr(
        backend_service_module,
        "complete_experiment",
        lambda **_kwargs: (_ for _ in ()).throw(ValueError("Experiment 4 not found.")),
    )

    try:
        backend_service_module.run_backend_pipeline(
            as_of_date=as_of_date,
            include_weekly_recommendation=True,
            retries=0,
        )
        assert False, "Expected backend pipeline to raise root cause error."
    except RuntimeError as exc:
        assert "root cause failure" in str(exc)
