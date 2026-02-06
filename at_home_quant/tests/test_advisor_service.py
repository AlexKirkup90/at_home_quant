import datetime

import pandas as pd
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
from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo
from at_home_quant.db.models import Base, PriceDaily, Ticker
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

    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {
        "QQQ": 0.22,
        "SPY": 0.10,
        "VMID": 0.05,
        "GLD": 0.03,
        "BIL": 0.00,
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


def test_weekly_advisor_flow_end_to_end():
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

        report = generate_weekly_recommendation(as_of_date=as_of, top_n=2, session=session)
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
