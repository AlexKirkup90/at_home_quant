import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.advisor.models import WorkflowDecisionInput
from at_home_quant.advisor.service import (
    generate_weekly_recommendation,
    get_latest_advisor_portfolio,
    get_latest_weekly_report,
    log_decision,
    save_advisor_portfolio_snapshot,
    save_executed_from_decisions,
)
from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo, TickerType
from at_home_quant.db.models import Base, DatasetSnapshot, PriceDaily, Ticker
from at_home_quant.portfolio.models import TargetPosition


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

        report = generate_weekly_recommendation(
            as_of_date=as_of, top_n=2, data_snapshot_hash="a" * 64, session=session
        )
        assert report.batch_id > 0
        assert report.recommendations
        latest_report = get_latest_weekly_report(as_of_date=as_of, session=session)
        assert latest_report is not None
        assert latest_report.batch_id == report.batch_id

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

        report = generate_weekly_recommendation(
            as_of_date=as_of, top_n=2, data_snapshot_hash="b" * 64, session=session
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

        with pytest.raises(ValueError, match="was not found"):
            generate_weekly_recommendation(
                as_of_date=as_of,
                top_n=2,
                data_snapshot_hash="f" * 64,
                session=session,
            )


def test_weekly_recommendation_is_deterministic_for_same_snapshot():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        snapshot_hash = "c" * 64
        _seed_feature_snapshot(session, as_of, snapshot_hash)
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
            session=session,
        )
        report_b = generate_weekly_recommendation(
            as_of_date=as_of,
            top_n=2,
            data_snapshot_hash=snapshot_hash,
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
