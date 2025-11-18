import datetime

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import TickerInfo, TickerType, UNIVERSE_BENCHMARK_SYMBOL, Universe
from at_home_quant.db.models import Base, PriceDaily, Ticker
from at_home_quant.regime.service import get_current_regime, get_universe_scores


def _seed_prices(session: Session, as_of_date: datetime.date) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_rows = {}
    for universe, symbol in UNIVERSE_BENCHMARK_SYMBOL.items():
        info = TickerInfo(symbol=symbol, name=symbol, asset_type=TickerType.ETF, universe=universe, currency="USD")
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

    dates = pd.bdate_range(end=as_of_date, periods=400)
    slopes = [0.05, 0.03, 0.02]
    for slope, (universe, symbol) in zip(slopes, UNIVERSE_BENCHMARK_SYMBOL.items()):
        for i, dt in enumerate(dates):
            price = 100 + i * slope
            session.add(
                PriceDaily(
                    ticker_id=ticker_rows[symbol],
                    date=dt.date(),
                    adj_close=price,
                )
            )
    session.commit()


def test_universe_scores_and_regime_selection():
    engine = create_engine("sqlite:///:memory:")
    with Session(engine) as session:
        as_of = datetime.date.today()
        _seed_prices(session, as_of)
        scores = get_universe_scores(as_of, session=session)
        assert len(scores) == 3
        decision = get_current_regime(as_of, session=session)
        assert decision.best_universe
        assert decision.best_universe_score == max(s.composite_score for s in decision.all_universe_scores)
