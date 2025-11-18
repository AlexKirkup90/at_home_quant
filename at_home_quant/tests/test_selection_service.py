import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerInfo, TickerType, Universe
from at_home_quant.db.models import Base, PriceDaily, Ticker
from at_home_quant.selection.service import rank_universe


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
