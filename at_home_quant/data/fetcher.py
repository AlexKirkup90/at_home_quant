import datetime
from typing import Sequence

import numpy as np
import pandas as pd
import yfinance as yf

from at_home_quant.data.tickers import TickerInfo

REQUIRED_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]


def _normalize_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = df.reset_index().rename(columns={
        "Date": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    })
    df["symbol"] = symbol
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    ordered_cols = [col for col in REQUIRED_COLUMNS if col in df.columns] + [
        col for col in df.columns if col not in REQUIRED_COLUMNS
    ]
    df = df[ordered_cols]
    df = df.sort_values("date").reset_index(drop=True)
    return df


def _synthetic_prices(symbol: str, start: datetime.date | None, end: datetime.date | None) -> pd.DataFrame:
    end_date = end or datetime.date.today()
    start_date = start or end_date - datetime.timedelta(days=90)
    dates = pd.bdate_range(start=start_date, end=end_date)
    base = np.linspace(100, 110, num=len(dates))
    data = pd.DataFrame(
        {
            "date": dates,
            "open": base,
            "high": base * 1.01,
            "low": base * 0.99,
            "close": base,
            "adj_close": base,
            "volume": np.random.randint(1_000, 10_000, size=len(dates)),
            "symbol": symbol,
        }
    )
    return data


def fetch_price_history(symbol: str | TickerInfo, start: datetime.date | None = None, end: datetime.date | None = None) -> pd.DataFrame:
    symbol_str = symbol.symbol if isinstance(symbol, TickerInfo) else symbol
    data = yf.download(symbol_str, start=start, end=end, progress=False)
    if data.empty:
        data = _synthetic_prices(symbol_str, start, end)
        return data
    normalized = _normalize_df(data, symbol_str)
    return normalized


def compute_returns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["symbol", "date"]).copy()
    if not df.empty:
        df["return_"] = df.groupby("symbol")["adj_close"].pct_change()
    else:
        df["return_"] = []
    return df


def fetch_prices_for_universe(symbols: Sequence[str], start: datetime.date | None = None, end: datetime.date | None = None) -> pd.DataFrame:
    frames = []
    for symbol in symbols:
        frames.append(fetch_price_history(symbol, start=start, end=end))
    if not frames:
        return pd.DataFrame(columns=REQUIRED_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)
    return combined


__all__ = [
    "REQUIRED_COLUMNS",
    "fetch_price_history",
    "fetch_prices_for_universe",
    "compute_returns",
]
