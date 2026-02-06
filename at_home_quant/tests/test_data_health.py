import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.health import get_data_health_report
from at_home_quant.data.tickers import BENCHMARKS
from at_home_quant.db.models import Base, PriceDaily, Ticker

REQUIRED_SYMBOLS = ["QQQ", "SPY", "VMID", "GLD", "BIL"]


def _seed_symbol_history(session: Session, symbol: str, as_of_date: datetime.date, periods: int = 260) -> None:
    info = BENCHMARKS[symbol]
    ticker = Ticker(
        symbol=info.symbol,
        name=info.name,
        asset_type=info.asset_type,
        universe=info.universe,
        currency=info.currency,
    )
    session.add(ticker)
    session.flush()

    dates = pd.bdate_range(end=as_of_date, periods=periods)
    for idx, dt in enumerate(dates):
        session.add(
            PriceDaily(
                ticker_id=ticker.id,
                date=dt.date(),
                adj_close=100.0 + idx,
            )
        )


def test_data_health_report_is_healthy_with_required_symbols_and_history():
    as_of = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        for symbol in REQUIRED_SYMBOLS:
            _seed_symbol_history(session, symbol, as_of, periods=260)
        session.commit()

        report = get_data_health_report(as_of_date=as_of, session=session)

    assert report.is_healthy
    assert report.latest_price_date == as_of
    assert not report.issues


def test_data_health_report_flags_missing_required_symbol():
    as_of = datetime.date(2025, 1, 31)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with Session(engine) as session:
        for symbol in ["QQQ", "SPY", "GLD", "BIL"]:
            _seed_symbol_history(session, symbol, as_of, periods=260)
        session.commit()

        report = get_data_health_report(as_of_date=as_of, session=session)

    assert not report.is_healthy
    assert any(issue.code == "missing_symbol_history" and "VMID" in issue.message for issue in report.issues)
