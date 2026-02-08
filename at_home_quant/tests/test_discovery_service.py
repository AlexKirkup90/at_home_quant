import datetime

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.advisor.service import save_advisor_portfolio_snapshot
from at_home_quant.data.tickers import BENCHMARKS, TickerInfo, TickerType, Universe
from at_home_quant.db.models import Base, PriceDaily, Ticker
from at_home_quant.discovery.service import (
    get_discovery_watchlist,
    get_latest_discovery_report,
    run_discovery_scan,
)
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


def _seed_prices(session: Session, as_of_date: datetime.date, periods: int = 320) -> None:
    Base.metadata.create_all(bind=session.bind)
    universe_members = {
        "QQQ": BENCHMARKS["QQQ"],
        "SPY": BENCHMARKS["SPY"],
        "VMID": BENCHMARKS["VMID"],
        "AAPL": TickerInfo("AAPL", "Apple Inc.", TickerType.EQUITY, Universe.NASDAQ100, "USD"),
        "MSFT": TickerInfo("MSFT", "Microsoft Corporation", TickerType.EQUITY, Universe.NASDAQ100, "USD"),
        "AMZN": TickerInfo("AMZN", "Amazon.com, Inc.", TickerType.EQUITY, Universe.SP500, "USD"),
        "GOOGL": TickerInfo("GOOGL", "Alphabet Inc.", TickerType.EQUITY, Universe.SP500, "USD"),
        "TSCO.L": TickerInfo("TSCO.L", "Tesco PLC", TickerType.EQUITY, Universe.FTSE250, "GBP"),
        "BVIC.L": TickerInfo("BVIC.L", "Britvic PLC", TickerType.EQUITY, Universe.FTSE250, "GBP"),
    }
    ticker_ids: dict[str, int] = {}
    for symbol, info in universe_members.items():
        ticker_ids[symbol] = _add_ticker(session, info)
    dates = pd.bdate_range(end=as_of_date, periods=periods)
    slopes = {
        "QQQ": 0.26,
        "SPY": 0.18,
        "VMID": 0.06,
        "AAPL": 0.24,
        "MSFT": 0.20,
        "AMZN": 0.15,
        "GOOGL": 0.12,
        "TSCO.L": 0.07,
        "BVIC.L": 0.05,
    }
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
                    volume=1_500_000,
                    return_=0.0,
                )
            )
    session.commit()


def test_discovery_scan_generates_tiered_candidates():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        save_advisor_portfolio_snapshot(
            as_of_date=as_of,
            positions=[
                TargetPosition("AAPL", 0.45, "equity"),
                TargetPosition("MSFT", 0.35, "equity"),
                TargetPosition("BIL", 0.20, "cash"),
            ],
            snapshot_type="executed",
            session=session,
        )
        report = run_discovery_scan(as_of_date=as_of, session=session)
        assert report.status == "succeeded"
        assert report.candidate_count > 0
        assert report.candidates
        assert any(candidate.tier in {"Watch Closely", "Consider Buy", "Strong Buy"} for candidate in report.candidates)

        latest = get_latest_discovery_report(as_of_date=as_of, session=session)
        assert latest is not None
        assert latest.run_id == report.run_id


def test_discovery_scan_excludes_benchmark_etfs():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        report = run_discovery_scan(as_of_date=as_of, session=session)
        symbols = {candidate.ticker for candidate in report.candidates}
        assert "QQQ" not in symbols
        assert "SPY" not in symbols
        assert "VMID" not in symbols


def test_discovery_watchlist_excludes_current_holdings():
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of)
        holdings = {"AAPL", "MSFT"}
        save_advisor_portfolio_snapshot(
            as_of_date=as_of,
            positions=[
                TargetPosition("AAPL", 0.45, "equity"),
                TargetPosition("MSFT", 0.35, "equity"),
                TargetPosition("BIL", 0.20, "cash"),
            ],
            snapshot_type="executed",
            session=session,
        )
        run_discovery_scan(as_of_date=as_of, session=session)
        watchlist = get_discovery_watchlist(as_of_date=as_of, limit=10, session=session)
        assert watchlist
        assert all(item.ticker not in holdings for item in watchlist)


def test_discovery_scan_respects_min_history_filter(monkeypatch):
    monkeypatch.setenv("DISCOVERY_MIN_HISTORY_DAYS", "500")
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2025, 2, 28)
    with Session(engine) as session:
        _seed_prices(session, as_of, periods=320)
        report = run_discovery_scan(as_of_date=as_of, session=session)
        assert report.status == "succeeded"
        assert report.candidate_count == 0
        excluded = report.summary.get("excluded_counts", {})
        assert excluded.get("insufficient_history", 0) > 0
