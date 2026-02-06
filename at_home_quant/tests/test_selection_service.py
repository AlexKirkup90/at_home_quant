import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerInfo, TickerType, Universe
from at_home_quant.db.models import Base, FundamentalSnapshot, PriceDaily, Ticker
from at_home_quant.selection.service import _point_in_time_fundamentals, rank_universe


def _seed_universe(session: Session, universe: Universe, symbols: list[str], as_of_date: datetime.date) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_rows = {}
    for symbol in symbols:
        info = TickerInfo(symbol=symbol, name=symbol, asset_type=TickerType.EQUITY, universe=universe, currency="USD")
        ticker = Ticker(
            symbol=info.symbol,
            name=info.name,
            asset_type=info.asset_type,
            universe=info.universe,
            currency=info.currency,
        )
        session.add(ticker)
        session.flush()
        ticker_rows[symbol] = ticker.id

    dates = pd.bdate_range(end=as_of_date, periods=300)
    slopes = [0.01 * (i + 1) for i in range(len(symbols))]
    for slope, symbol in zip(slopes, symbols):
        for idx, dt in enumerate(dates):
            price = 50 + idx * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_rows[symbol],
                    date=dt.date(),
                    adj_close=price,
                )
            )
    session.commit()


def test_rank_universe_end_to_end():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        as_of = datetime.date.today()
        symbols = ["AAA", "BBB", "CCC", "DDD"]
        _seed_universe(session, Universe.NASDAQ100, symbols, as_of)
        scores = rank_universe("NASDAQ100", as_of, top_n=3, session=session)
        assert len(scores) == 3
        composites = [s.composite_score for s in scores]
        assert composites == sorted(composites, reverse=True)
        assert all(s.ticker in symbols for s in scores)


def test_point_in_time_fundamentals_do_not_use_future_snapshot():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        as_of = datetime.date(2025, 3, 31)
        symbols = ["AAA"]
        _seed_universe(session, Universe.NASDAQ100, symbols, as_of)
        ticker = session.query(Ticker).filter(Ticker.symbol == "AAA").one()
        session.add(
            FundamentalSnapshot(
                ticker_id=ticker.id,
                as_of_date=datetime.date(2025, 2, 15),
                value_score=0.10,
                shareholder_yield_score=0.20,
            )
        )
        session.add(
            FundamentalSnapshot(
                ticker_id=ticker.id,
                as_of_date=datetime.date(2025, 4, 15),
                value_score=0.95,
                shareholder_yield_score=0.90,
            )
        )
        session.commit()

        early_value, early_shareholder = _point_in_time_fundamentals(
            session=session,
            symbol="AAA",
            as_of_date=datetime.date(2025, 3, 1),
        )
        later_value, later_shareholder = _point_in_time_fundamentals(
            session=session,
            symbol="AAA",
            as_of_date=datetime.date(2025, 5, 1),
        )

    assert early_value == 0.10
    assert early_shareholder == 0.20
    assert later_value == 0.95
    assert later_shareholder == 0.90
