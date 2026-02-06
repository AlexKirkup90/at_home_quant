from __future__ import annotations

import datetime
from typing import Sequence

import pandas as pd
import yfinance as yf
from sqlalchemy import select

from at_home_quant.config.settings import get_settings
from at_home_quant.data.fetcher import compute_returns
from at_home_quant.data.tickers import ALL_TICKERS, list_all_symbols
from at_home_quant.db import crud
from at_home_quant.db.models import Ticker
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
        field_names = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}
        level_0 = set(df.columns.get_level_values(0))
        level_1 = set(df.columns.get_level_values(1))
        stack_level = 0 if field_names.intersection(level_1) else 1 if field_names.intersection(level_0) else 0
        try:
            stacked = df.stack(level=stack_level, future_stack=True).rename_axis(["date", "symbol"]).reset_index()
        except TypeError:  # pandas<2.1
            stacked = df.stack(level=stack_level).rename_axis(["date", "symbol"]).reset_index()
    else:
        stacked = df.reset_index().rename(columns={"Date": "date", "index": "date"}).copy()
        if "symbol" not in stacked.columns:
            stacked["symbol"] = symbol or ""

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
    elif "close" in normalized.columns:
        normalized["adj_close"] = normalized["close"]

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
    normalized = normalized.dropna(subset=["date", "symbol", "close"], how="any")
    if "adj_close" in normalized.columns:
        normalized["adj_close"] = normalized["adj_close"].fillna(normalized["close"])

    normalized = normalized.sort_values(["symbol", "date"]).reset_index(drop=True)
    required = {"date", "symbol", "close"}
    missing = required - set(normalized.columns)
    if missing:
        raise ValueError(f"Normalized data is missing required columns: {missing}")
    return normalized


def run_full_history(start: datetime.date | None = None, end: datetime.date | None = None) -> None:
    settings = get_settings()
    init_db()
    with get_session() as session:
        crud.upsert_tickers(session, ALL_TICKERS)
        symbols: Sequence[str] = sorted(
            set(list_all_symbols())
            | set(session.execute(select(Ticker.symbol).order_by(Ticker.symbol)).scalars().all())
        )

    start_date = start or settings.default_start_date

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
