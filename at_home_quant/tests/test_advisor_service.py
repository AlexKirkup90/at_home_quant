import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from at_home_quant.advisor.models import WorkflowDecisionInput
from at_home_quant.advisor.service import (
    generate_weekly_recommendation,
    get_latest_advisor_portfolio,
    get_weekly_outcome_trend,
    get_weekly_outcome_report,
    get_latest_weekly_report,
    log_decision,
    save_advisor_portfolio_snapshot,
    save_executed_from_decisions,
    upsert_weekly_outcome_metrics,
)
from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo, TickerType
from at_home_quant.db.models import (
    Base,
    DatasetSnapshot,
    PriceDaily,
    Ticker,
    WeeklyOutcomeMetric,
    WeeklyRecommendationBatch,
)
from at_home_quant.portfolio.models import TargetPosition
from at_home_quant.research.registry import register_experiment


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


def _seed_prices(session: Session, as_of_date: datetime.date, periods: int = 420) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_ids: dict[str, int] = {}
    for symbol in ["QQQ", "SPY", "VMID", "GLD", "BIL"]:
        ticker_ids[symbol] = _add_ticker(session, BENCHMARKS[symbol])
    for info in SAMPLE_NASDAQ100.values():
        ticker_ids[info.symbol] = _add_ticker(session, info)
    for symbol in ["SGLN", "VAGS", "VHYL", "VUSA"]:
        ticker_ids[symbol] = _add_ticker(
            session,
            TickerInfo(
                symbol=symbol,
                name=symbol,
                asset_type=TickerType.EQUITY,
                universe=None,
                currency="USD",
            ),
        )

    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {
        "QQQ": 0.22,
        "SPY": 0.10,
        "VMID": 0.05,
        "GLD": 0.03,
        "BIL": 0.00,
        "SGLN": 0.03,
        "VAGS": 0.00,
        "VHYL": 0.11,
        "VUSA": 0.10,
        "AAPL": 0.24,
        "MSFT": 0.20,
    }
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes.get(symbol, 0.1)
        for idx, dt in enumerate(dates):
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    adj_close=100 + idx * slope,
                    volume=2_000_000,
                )
            )
    session.commit()


def _seed_feature_snapshot(session: Session, as_of_date: datetime.date, snapshot_hash: str) -> None:
    session.add(
        DatasetSnapshot(
            layer="feature",
            as_of_date=as_of_date,
            snapshot_hash=snapshot_hash,
            row_count=1,
            run_id=None,
        )
    )
    session.commit()


def _seed_experiment(session: Session, as_of_date: datetime.date, snapshot_hash: str, run_type: str = "backend_weekly") -> int:
    row = register_experiment(
        session=session,
        run_type=run_type,
        as_of_date=as_of_date,
        feature_snapshot_hash=snapshot_hash,
        params={"top_n": 2},
        window=None,
    )
    session.commit()
    return row.id


def test_weekly_advisor_flow_end_to_end():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        _seed_feature_snapshot(session, as_of, "a" * 64)
        positions = [
            TargetPosition("AAPL", 0.40, "equity"),
            TargetPosition("MSFT", 0.35, "equity"),
            TargetPosition("GLD", 0.10, "gold"),
            TargetPosition("BIL", 0.15, "cash"),
        ]
        save_advisor_portfolio_snapshot(
            as_of_date=as_of,
            positions=positions,
            snapshot_type="baseline",
            session=session,
        )
        save_advisor_portfolio_snapshot(
            as_of_date=as_of,
            positions=positions,
            snapshot_type="executed",
            session=session,
        )
        experiment_id = _seed_experiment(session, as_of, "a" * 64)

        report = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash="a" * 64,
            experiment_id=experiment_id,
            session=session,
        )
        assert report.batch_id > 0
        assert report.recommendations
        assert report.experiment_id == experiment_id
        assert "blocked_count" in report.pretrade_summary
        latest_report = get_latest_weekly_report(as_of_date=as_of, session=session)
        assert latest_report is not None
        assert latest_report.batch_id == report.batch_id
        assert latest_report.experiment_id == experiment_id
        assert "estimated_shortfall_pct" in latest_report.pretrade_summary

        for item in report.recommendations[:3]:
            log_decision(
                WorkflowDecisionInput(
                    item_id=item.id,
                    decision="follow",
                ),
                session=session,
            )
        execution = save_executed_from_decisions(report.batch_id, session=session)
        assert execution.positions
        assert abs(sum(position.weight for position in execution.positions) - 1.0) < 1e-6
        latest_executed = get_latest_advisor_portfolio(
            "executed",
            as_of_date=as_of,
            session=session,
        )
        assert latest_executed is not None


def test_weekly_recommendation_applies_equivalence_and_trade_gating(monkeypatch):
    monkeypatch.setenv("RESPECT_CURRENT_BOOK_MODE", "true")
    monkeypatch.setenv("MIN_TRADE_DELTA_PCT", "5.0")
    monkeypatch.setenv("WEIGHT_ROUNDING_PCT", "1.0")
    monkeypatch.setenv("ENABLE_TRADE_GATING", "true")
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        _seed_feature_snapshot(session, as_of, "b" * 64)
        positions = [
            TargetPosition("VHYL", 0.22, "equity"),
            TargetPosition("VUSA", 0.24, "equity"),
            TargetPosition("SGLN", 0.14, "gold"),
            TargetPosition("VAGS", 0.40, "cash"),
        ]
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="baseline", session=session)
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="executed", session=session)
        experiment_id = _seed_experiment(session, as_of, "b" * 64)

        report = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash="b" * 64,
            experiment_id=experiment_id,
            session=session,
        )
        by_ticker = {item.ticker: item for item in report.recommendations}
        # Equivalent mapping should keep user sleeve tickers instead of forcing GLD/BIL.
        assert "GLD" not in by_ticker
        assert "BIL" not in by_ticker
        assert "SGLN" in by_ticker
        assert "VAGS" in by_ticker
        # Practical trade gate should suppress small moves.
        small_moves = [item for item in report.recommendations if abs(item.delta) < 0.05]
        assert small_moves
        assert all(item.recommendation == "hold" for item in small_moves)
        assert all(item.rationale.startswith("[") for item in report.recommendations)


def test_weekly_recommendation_requires_valid_feature_snapshot_hash():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        _seed_feature_snapshot(session, as_of, "d" * 64)
        positions = [
            TargetPosition("AAPL", 0.40, "equity"),
            TargetPosition("MSFT", 0.35, "equity"),
            TargetPosition("GLD", 0.10, "gold"),
            TargetPosition("BIL", 0.15, "cash"),
        ]
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="baseline", session=session)
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="executed", session=session)

        with pytest.raises(ValueError, match="requires a data snapshot hash"):
            generate_weekly_recommendation(as_of_date=as_of, top_n=2, session=session)

        with pytest.raises(ValueError, match="requires an experiment id"):
            generate_weekly_recommendation(
                as_of_date=as_of,
                top_n=2,
                data_snapshot_hash="d" * 64,
                session=session,
            )

        with pytest.raises(ValueError, match="was not found"):
            generate_weekly_recommendation(
                as_of_date=as_of,
                top_n=2,
                data_snapshot_hash="f" * 64,
                experiment_id=123,
                session=session,
            )


def test_weekly_recommendation_is_deterministic_for_same_snapshot():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        snapshot_hash = "c" * 64
        _seed_feature_snapshot(session, as_of, snapshot_hash)
        experiment_id = _seed_experiment(session, as_of, snapshot_hash)
        positions = [
            TargetPosition("AAPL", 0.40, "equity"),
            TargetPosition("MSFT", 0.35, "equity"),
            TargetPosition("GLD", 0.10, "gold"),
            TargetPosition("BIL", 0.15, "cash"),
        ]
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="baseline", session=session)
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="executed", session=session)

        report_a = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash=snapshot_hash,
            experiment_id=experiment_id,
            session=session,
        )
        report_b = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash=snapshot_hash,
            experiment_id=experiment_id,
            session=session,
        )

        items_a = sorted(
            (
                item.ticker,
                item.recommendation,
                round(item.current_weight, 8),
                round(item.target_weight, 8),
                round(item.delta, 8),
                item.rationale,
            )
            for item in report_a.recommendations
        )
        items_b = sorted(
            (
                item.ticker,
                item.recommendation,
                round(item.current_weight, 8),
                round(item.target_weight, 8),
                round(item.delta, 8),
                item.rationale,
            )
            for item in report_b.recommendations
        )
        assert report_a.best_universe == report_b.best_universe
        assert round(report_a.best_universe_score, 8) == round(report_b.best_universe_score, 8)
        assert items_a == items_b


def test_weekly_outcome_report_computes_decision_alpha():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 20)
    future_end = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, future_end)
        snapshot_hash = "d" * 64
        _seed_feature_snapshot(session, as_of, snapshot_hash)
        experiment_id = _seed_experiment(session, as_of, snapshot_hash)
        positions = [
            TargetPosition("AAPL", 0.40, "equity"),
            TargetPosition("MSFT", 0.35, "equity"),
            TargetPosition("GLD", 0.10, "gold"),
            TargetPosition("BIL", 0.15, "cash"),
        ]
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="baseline", session=session)
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="executed", session=session)

        report = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash=snapshot_hash,
            experiment_id=experiment_id,
            session=session,
        )
        for item in report.recommendations[:2]:
            log_decision(
                WorkflowDecisionInput(
                    item_id=item.id,
                    decision="follow",
                ),
                session=session,
            )
        outcome = get_weekly_outcome_report(batch_id=report.batch_id, horizon_days=7, session=session)
        assert outcome is not None
        assert outcome.evaluation_date >= as_of
        assert outcome.items
        assert isinstance(outcome.decision_alpha, float)
        assert outcome.model_implementation_shortfall >= 0
        assert outcome.decision_implementation_shortfall >= 0


def test_weekly_outcome_metrics_upsert_is_idempotent():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 20)
    future_end = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, future_end)
        snapshot_hash = "e" * 64
        _seed_feature_snapshot(session, as_of, snapshot_hash)
        experiment_id = _seed_experiment(session, as_of, snapshot_hash)
        positions = [
            TargetPosition("AAPL", 0.40, "equity"),
            TargetPosition("MSFT", 0.35, "equity"),
            TargetPosition("GLD", 0.10, "gold"),
            TargetPosition("BIL", 0.15, "cash"),
        ]
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="baseline", session=session)
        save_advisor_portfolio_snapshot(as_of_date=as_of, positions=positions, snapshot_type="executed", session=session)

        report = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash=snapshot_hash,
            experiment_id=experiment_id,
            session=session,
        )
        first = upsert_weekly_outcome_metrics(batch_id=report.batch_id, horizon_days=7, session=session)
        second = upsert_weekly_outcome_metrics(batch_id=report.batch_id, horizon_days=7, session=session)
        assert first is not None
        assert second is not None

        rows = session.execute(
            select(WeeklyOutcomeMetric).where(
                WeeklyOutcomeMetric.batch_id == report.batch_id,
                WeeklyOutcomeMetric.horizon_days == 7,
            )
        ).scalars().all()
        assert len(rows) == 1


def test_weekly_outcome_trend_flags_negative_alpha_streak():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    as_of_dates = [datetime.date(2025, 1, 3) + datetime.timedelta(days=7 * idx) for idx in range(5)]
    with Session(engine) as session:
        alpha_values = [0.005, -0.01, -0.02, -0.015, -0.03]
        shortfall_values = [0.001, 0.001, 0.001, 0.002, 0.003]
        for idx, as_of_date in enumerate(as_of_dates):
            batch = WeeklyRecommendationBatch(
                as_of_date=as_of_date,
                best_universe="NASDAQ100",
                best_universe_score=70.0,
                status="closed",
                data_snapshot_hash="a" * 64,
                watchlist_json="[]",
            )
            session.add(batch)
            session.flush()
            session.add(
                WeeklyOutcomeMetric(
                    batch_id=batch.id,
                    as_of_date=as_of_date,
                    evaluation_date=as_of_date + datetime.timedelta(days=7),
                    horizon_days=7,
                    item_count=10,
                    model_active_return=0.0,
                    decision_active_return=alpha_values[idx],
                    decision_alpha=alpha_values[idx],
                    follow_hit_rate=0.4,
                    ignored_positive_count=2,
                    model_portfolio_return=0.0,
                    decision_portfolio_return=0.0,
                    benchmark_return=0.0,
                    model_vs_benchmark=0.0,
                    decision_vs_benchmark=alpha_values[idx],
                    model_implementation_shortfall=0.001,
                    decision_implementation_shortfall=0.001 + shortfall_values[idx],
                    shortfall_gap=shortfall_values[idx],
                )
            )
        session.flush()

        trend = get_weekly_outcome_trend(horizon_days=7, lookback=12, rolling_window=4, session=session)
        assert len(trend.points) == 5
        assert trend.flag_negative_decision_alpha_streak
        assert trend.flag_negative_rolling_decision_alpha
        assert trend.flag_rising_shortfall_gap
