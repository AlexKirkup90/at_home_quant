import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerInfo, TickerType, Universe
from at_home_quant.db.models import Base, PriceDaily, Ticker, UniverseMembership
from at_home_quant.selection.service import rank_universe


def _add_ticker(session: Session, symbol: str) -> int:
    info = TickerInfo(
        symbol=symbol,
        name=symbol,
        asset_type=TickerType.EQUITY,
        universe=Universe.NASDAQ100,
        currency="USD",
    )
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


def _seed_prices(session: Session, ticker_id: int, as_of: datetime.date, slope: float) -> None:
    dates = pd.bdate_range(end=as_of, periods=320)
    for idx, dt in enumerate(dates):
        session.add(
            PriceDaily(
                ticker_id=ticker_id,
                date=dt.date(),
                adj_close=50 + idx * slope,
                volume=1_000_000,
            )
        )


def test_rank_universe_uses_point_in_time_membership():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        aaa_id = _add_ticker(session, "AAA")
        bbb_id = _add_ticker(session, "BBB")
        as_of = datetime.date(2025, 2, 28)
        _seed_prices(session, aaa_id, as_of, slope=0.02)
        _seed_prices(session, bbb_id, as_of, slope=0.04)
        session.add_all(
            [
                UniverseMembership(
                    ticker_id=aaa_id,
                    universe=Universe.NASDAQ100,
                    effective_from=datetime.date(2020, 1, 1),
                    effective_to=datetime.date(2025, 1, 31),
                ),
                UniverseMembership(
                    ticker_id=bbb_id,
                    universe=Universe.NASDAQ100,
                    effective_from=datetime.date(2025, 2, 1),
                    effective_to=None,
                ),
            ]
        )
        session.commit()

        january_scores = rank_universe("NASDAQ100", datetime.date(2025, 1, 31), top_n=5, session=session)
        february_scores = rank_universe("NASDAQ100", datetime.date(2025, 2, 28), top_n=5, session=session)

        assert {item.ticker for item in january_scores} == {"AAA"}
        assert {item.ticker for item in february_scores} == {"BBB"}
