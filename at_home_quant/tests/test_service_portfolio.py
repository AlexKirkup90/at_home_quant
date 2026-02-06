import datetime
import json

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import BENCHMARKS, SAMPLE_NASDAQ100, TickerInfo
from at_home_quant.db.models import Base, PortfolioSnapshot, PriceDaily, Ticker
from at_home_quant.portfolio.models import TargetPosition
from at_home_quant.portfolio.service import (
    build_monthly_portfolio,
    compute_rebalance,
    save_manual_portfolio_snapshot,
)


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


def _seed_prices(session: Session, as_of_date: datetime.date, periods: int = 400) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_ids: dict[str, int] = {}
    benchmark_symbols = ["QQQ", "SPY", "VMID", "GLD", "BIL"]
    for symbol in benchmark_symbols:
        ticker_ids[symbol] = _add_ticker(session, BENCHMARKS[symbol])
    for info in SAMPLE_NASDAQ100.values():
        ticker_ids[info.symbol] = _add_ticker(session, info)

    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {
        "QQQ": 0.2,
        "SPY": 0.1,
        "VMID": 0.05,
        "GLD": 0.03,
        "BIL": 0.0,
        "AAPL": 0.25,
        "MSFT": 0.22,
    }
    for symbol, ticker_id in ticker_ids.items():
        slope = slopes.get(symbol, 0.1)
        for i, dt in enumerate(dates):
            price = 100 + i * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_id,
                    date=dt.date(),
                    adj_close=price,
                    volume=2_000_000,
                )
            )
    session.commit()


def test_end_to_end_portfolio_and_rebalance():
    as_of_first = datetime.date(2024, 12, 31)
    as_of_second = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, as_of_second)
        portfolio = build_monthly_portfolio(as_of_first, session=session)
        assert abs(sum(p.weight for p in portfolio.positions) - 1.0) < 1e-6
        assert portfolio.universe_name == "NASDAQ100"
        assert portfolio.risk_report is not None
        assert portfolio.risk_report.is_within_limits
        assert session.query(PortfolioSnapshot).count() == 1

        instructions = compute_rebalance(as_of_second, session=session)
        assert instructions
        assert all(instr.action in {"buy", "sell", "hold"} for instr in instructions)
        assert all(instr.policy_status in {"pass", "blocked"} for instr in instructions)
        # Rebalance is read-only and must not write a new snapshot.
        assert session.query(PortfolioSnapshot).count() == 1


def test_data_health_gate_blocks_portfolio_when_history_is_insufficient():
    as_of = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, as_of, periods=120)
        with pytest.raises(ValueError, match="Data health gate failed"):
            build_monthly_portfolio(as_of, session=session)


def test_risk_overlay_blocks_snapshot_save_when_constraints_breached(monkeypatch):
    as_of_first = datetime.date(2024, 12, 31)
    as_of_second = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, as_of_second)
        session.add(
            PortfolioSnapshot(
                as_of_date=as_of_first,
                universe_name="NASDAQ100",
                equity_exposure=1.0,
                defensive_exposure=0.0,
                positions_json=json.dumps([{"ticker": "AAPL", "weight": 1.0, "asset_type": "equity"}]),
            )
        )
        session.commit()
        monkeypatch.setenv("DATA_MODE", "production")
        monkeypatch.setenv("RISK_MAX_TURNOVER", "0.05")
        monkeypatch.setenv("RISK_MAX_POSITION", "0.12")
        with pytest.raises(ValueError, match="Risk overlay gate failed"):
            build_monthly_portfolio(as_of_second, session=session)


def test_save_manual_portfolio_snapshot_persists_anchor():
    as_of = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        _seed_prices(session, as_of)
        portfolio = save_manual_portfolio_snapshot(
            as_of_date=as_of,
            positions=[
                TargetPosition("VUSA", 0.50, "equity"),
                TargetPosition("SGLN", 0.25, "gold"),
                TargetPosition("VAGS", 0.25, "cash"),
            ],
            session=session,
        )
        assert portfolio.universe_name == "USER_BASELINE"
        assert abs(portfolio.equity_exposure - 0.50) < 1e-6
        assert abs(portfolio.defensive_exposure - 0.50) < 1e-6
        saved = session.query(PortfolioSnapshot).filter(PortfolioSnapshot.as_of_date == as_of).one()
        saved_positions = json.loads(saved.positions_json)
        assert len(saved_positions) == 3
        assert saved_positions[0]["ticker"] == "VUSA"
