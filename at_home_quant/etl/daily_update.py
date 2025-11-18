import datetime
from typing import Sequence

import pandas as pd
from sqlalchemy import select

from at_home_quant.config.settings import get_settings
from at_home_quant.data.fetcher import compute_returns, fetch_prices_for_universe
from at_home_quant.data.tickers import ALL_TICKERS, list_all_symbols
from at_home_quant.db import crud
from at_home_quant.db.models import PriceDaily, Ticker
from at_home_quant.db.session import get_session, init_db


def _get_latest_dates(session) -> dict[str, datetime.date | None]:
    stmt = (
        select(Ticker.symbol, PriceDaily.date)
        .join(PriceDaily, PriceDaily.ticker_id == Ticker.id)
        .order_by(Ticker.symbol, PriceDaily.date.desc())
    )
    rows = session.execute(stmt).all()
    latest: dict[str, datetime.date | None] = {}
    for symbol, date in rows:
        if symbol not in latest:
            latest[symbol] = date
    return latest


def run_daily_update() -> None:
    settings = get_settings()
    init_db()
    with get_session() as session:
        crud.upsert_tickers(session, ALL_TICKERS)

    with get_session() as session:
        latest_dates = _get_latest_dates(session)

    today = datetime.date.today()
    fetch_start_by_symbol: dict[str, datetime.date] = {}
    for symbol in list_all_symbols():
        last_date = latest_dates.get(symbol)
        if last_date:
            fetch_start_by_symbol[symbol] = last_date + datetime.timedelta(days=1)
        else:
            fetch_start_by_symbol[symbol] = settings.default_start_date

    symbols: Sequence[str] = list_all_symbols()
    frames = []
    for symbol in symbols:
        start_date = fetch_start_by_symbol[symbol]
        if start_date > today:
            continue
        prices = fetch_prices_for_universe([symbol], start=start_date, end=None)
        frames.append(prices)

    if not frames:
        return

    combined = compute_returns((frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)))
    with get_session() as session:
        crud.upsert_prices(session, combined)


if __name__ == "__main__":
    run_daily_update()
