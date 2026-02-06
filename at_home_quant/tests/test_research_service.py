import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo
from at_home_quant.db.models import Base, DatasetSnapshot, ExperimentRun, PriceDaily, Ticker
from at_home_quant.research.models import ExperimentWindow
from at_home_quant.research.registry import leakage_issues
from at_home_quant.research.service import run_walk_forward_experiment


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


def _seed_prices(session: Session, as_of_date: datetime.date, periods: int = 520) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_ids: dict[str, int] = {}
    for symbol in ["QQQ", "SPY", "VMID", "GLD", "BIL"]:
        ticker_ids[symbol] = _add_ticker(session, BENCHMARKS[symbol])
    for info in SAMPLE_NASDAQ100.values():
        ticker_ids[info.symbol] = _add_ticker(session, info)

    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {"QQQ": 0.2, "SPY": 0.1, "VMID": 0.05, "GLD": 0.03, "BIL": 0.0, "AAPL": 0.28, "MSFT": 0.24}
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes.get(symbol, 0.1)
        for idx, dt in enumerate(dates):
            price = 100 + idx * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    adj_close=price,
                    volume=2_000_000,
                )
            )
    session.commit()


def test_leakage_issues_detect_invalid_boundaries():
    bad_window = ExperimentWindow(
        train_start=datetime.date(2025, 1, 1),
        train_end=datetime.date(2025, 6, 1),
        validation_start=datetime.date(2025, 5, 1),
        validation_end=datetime.date(2025, 8, 1),
        holdout_start=datetime.date(2025, 7, 1),
        holdout_end=datetime.date(2025, 12, 1),
    )
    issues = leakage_issues(bad_window)
    assert issues
    assert "train_end < validation_start" in issues
    assert "validation_end < holdout_start" in issues


def test_run_walk_forward_experiment_registers_model_report(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 4, 30)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        session.add(
            DatasetSnapshot(
                layer="feature",
                as_of_date=as_of,
                snapshot_hash="f" * 64,
                row_count=1,
                run_id=None,
            )
        )
        session.commit()

    def get_session_override():
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

    monkeypatch.setattr("at_home_quant.research.service.get_session", get_session_override)
    monkeypatch.setattr("at_home_quant.backtest.service.get_session", get_session_override)

    report = run_walk_forward_experiment(
        end_date=as_of,
        top_n=2,
        train_months=6,
        validation_months=3,
        holdout_months=3,
    )

    assert report.experiment_id > 0
    assert report.linked_run_id is not None
    assert report.challenger_comparison
    assert "challengers" in report.challenger_comparison
    assert "regime_stability" in report.robustness_checks

    with Session(engine) as session:
        row = session.query(ExperimentRun).filter(ExperimentRun.id == report.experiment_id).one()
        assert row.status == "succeeded"
        assert row.leakage_checks_passed == 1
