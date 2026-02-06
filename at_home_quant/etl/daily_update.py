from __future__ import annotations

import datetime
import logging
from typing import Sequence

import pandas as pd
from sqlalchemy import select

from at_home_quant.config.settings import get_settings
from at_home_quant.data.health import get_portfolio_required_symbols
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


def _load_symbol_universe(session) -> list[str]:
    db_symbols = session.execute(select(Ticker.symbol).order_by(Ticker.symbol)).scalars().all()
    return sorted(set(list_all_symbols()) | set(db_symbols))


def run_daily_update() -> None:
    settings = get_settings()
    today = datetime.date.today()
    init_db()
    with get_session() as session:
        crud.upsert_tickers(session, ALL_TICKERS)

    with get_session() as session:
        latest_dates = _get_latest_dates(session)
        symbols = _load_symbol_universe(session)
        required_symbols = set(get_portfolio_required_symbols(as_of_date=today, session=session))
    fetch_start_by_symbol: dict[str, datetime.date] = {}
    for symbol in symbols:
        last_date = latest_dates.get(symbol)
        if last_date:
            # Start from the latest stored point instead of +1 day.
            # This keeps incremental updates idempotent and avoids false empty
            # responses when same-day market bars are not yet available.
            fetch_start_by_symbol[symbol] = last_date
        else:
            fetch_start_by_symbol[symbol] = settings.default_start_date

    frames = []
    for symbol in symbols:
        start_date = fetch_start_by_symbol[symbol]
        # Skip same-day fetches; intraday/partial vendor responses are often empty.
        # The next backend run will pick up that bar once it is published.
        if start_date >= today:
            continue
        try:
            prices = fetch_prices_for_universe([symbol], start=start_date, end=None)
        except Exception as exc:  # noqa: BLE001
            if symbol in required_symbols:
                raise
            logging.getLogger(__name__).warning(
                "Skipping non-core symbol %s due to fetch failure: %s",
                symbol,
                exc,
            )
            continue
        frames.append(prices)

    if not frames:
        return

    combined = compute_returns((frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)))
    with get_session() as session:
        crud.upsert_prices(session, combined)


if __name__ == "__main__":
    run_daily_update()
