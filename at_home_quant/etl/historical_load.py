import datetime
from typing import Sequence

from at_home_quant.config.settings import get_settings
from at_home_quant.data.fetcher import compute_returns, fetch_prices_for_universe
from at_home_quant.data.tickers import ALL_TICKERS, list_all_symbols
from at_home_quant.db import crud
from at_home_quant.db.session import get_session, init_db


def run_full_history(start: datetime.date | None = None, end: datetime.date | None = None) -> None:
    settings = get_settings()
    init_db()
    with get_session() as session:
        crud.upsert_tickers(session, ALL_TICKERS)

    start_date = start or settings.default_start_date
    symbols: Sequence[str] = list_all_symbols()
    prices = fetch_prices_for_universe(symbols, start=start_date, end=end)
    prices = compute_returns(prices)

    with get_session() as session:
        crud.upsert_prices(session, prices)


if __name__ == "__main__":
    run_full_history()
