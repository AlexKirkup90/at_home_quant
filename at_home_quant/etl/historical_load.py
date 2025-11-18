import datetime
from typing import Sequence

import pandas as pd
import yfinance as yf

from at_home_quant.config.settings import get_settings
from at_home_quant.data.fetcher import compute_returns
from at_home_quant.data.tickers import ALL_TICKERS, list_all_symbols
from at_home_quant.db import crud
from at_home_quant.db.session import get_session, init_db


def normalize_yfinance_prices(df: pd.DataFrame, symbol: str | None = None) -> pd.DataFrame:
    """
    Normalize yfinance download output to a flat DataFrame with columns:
    date, open, high, low, close, adj_close, volume, symbol.

    Handles both single-ticker and multi-ticker MultiIndex formats.
    """

    if df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        stacked = df.stack(level=-1).rename_axis(["date", "symbol"]).reset_index()
    else:
        stacked = df.reset_index().rename(columns={"Date": "date"}).copy()
        stacked["symbol"] = stacked.get("symbol", symbol or "")

    field_map = {
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    }

    available = {key: value for key, value in field_map.items() if key in stacked.columns}
    normalized = stacked.rename(columns=available)

    if "adj_close" in normalized.columns:
        normalized["close"] = normalized["adj_close"]

    if "date" in normalized.columns:
        normalized["date"] = pd.to_datetime(normalized["date"])

    desired_order = [
        "date",
        "symbol",
        "open",
        "high",
        "low",
        "close",
        "adj_close",
        "volume",
    ]
    columns = [col for col in desired_order if col in normalized.columns]
    normalized = normalized[columns]

    normalized = normalized.sort_values(["symbol", "date"]).reset_index(drop=True)
    return normalized


def run_full_history(start: datetime.date | None = None, end: datetime.date | None = None) -> None:
    settings = get_settings()
    init_db()
    with get_session() as session:
        crud.upsert_tickers(session, ALL_TICKERS)

    start_date = start or settings.default_start_date
    symbols: Sequence[str] = list_all_symbols()

    raw_prices = yf.download(
        tickers=" ".join(symbols),
        start=start_date,
        end=end,
        group_by="ticker",
        auto_adjust=False,
        progress=False,
    )

    prices = normalize_yfinance_prices(raw_prices)
    prices = compute_returns(prices)

    with get_session() as session:
        crud.upsert_prices(session, prices)


if __name__ == "__main__":
    run_full_history()
