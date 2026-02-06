from __future__ import annotations

import datetime
from typing import Sequence

import numpy as np
import pandas as pd
import yfinance as yf

from at_home_quant.config.settings import get_settings
from at_home_quant.data.tickers import TickerInfo, vendor_symbol_candidates

REQUIRED_COLUMNS = ["symbol", "date", "open", "high", "low", "close", "adj_close", "volume"]


def _flatten_single_symbol_yfinance(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if not isinstance(df.columns, pd.MultiIndex):
        return df

    level_0 = set(df.columns.get_level_values(0))
    level_1 = set(df.columns.get_level_values(1))
    field_names = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}

    if symbol in level_1:
        return df.xs(symbol, axis=1, level=1, drop_level=True)
    if symbol in level_0:
        return df.xs(symbol, axis=1, level=0, drop_level=True)

    if field_names.intersection(level_0):
        fallback_symbol = df.columns.get_level_values(1)[0]
        return df.xs(fallback_symbol, axis=1, level=1, drop_level=True)

    fallback_symbol = df.columns.get_level_values(0)[0]
    return df.xs(fallback_symbol, axis=1, level=0, drop_level=True)


def _normalize_df(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    df = _flatten_single_symbol_yfinance(df, symbol)
    df = df.reset_index().rename(columns={
        "Date": "date",
        "index": "date",
        "Open": "open",
        "High": "high",
        "Low": "low",
        "Close": "close",
        "Adj Close": "adj_close",
        "Volume": "volume",
    })
    if "adj_close" in df.columns:
        df["close"] = df["adj_close"]
    elif "close" in df.columns:
        df["adj_close"] = df["close"]
    df["symbol"] = symbol
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    required = {"date", "close", "adj_close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Normalized single-symbol data missing columns: {missing}")
    df = df.dropna(subset=["date", "close", "adj_close"])
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
    settings = get_settings()
    last_error: Exception | None = None
    for vendor_symbol in vendor_symbol_candidates(symbol_str):
        try:
            data = yf.download(vendor_symbol, start=start, end=end, progress=False)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        if data.empty:
            continue
        try:
            normalized = _normalize_df(data, symbol_str)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue
        return normalized

    if settings.allow_synthetic_data:
        return _synthetic_prices(symbol_str, start, end)
    if last_error is not None:
        raise RuntimeError(
            f"Price fetch failed for {symbol_str} using aliases {vendor_symbol_candidates(symbol_str)}: {last_error}"
        ) from last_error
    raise RuntimeError(
        f"No market data returned for {symbol_str} using aliases {vendor_symbol_candidates(symbol_str)} "
        f"between {start} and {end} in production mode."
    )


def compute_returns(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        # ensure we return a DataFrame with the same columns plus an empty return_ column
        result = df.copy()
        result["return_"] = pd.Series(dtype=float)
        return result

    result = df.copy()
    result["date"] = pd.to_datetime(result["date"])
    result = result.sort_values(["symbol", "date"])
    result["return_"] = (
        result.groupby("symbol")["close"].pct_change().fillna(0.0)
    )
    return result


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
