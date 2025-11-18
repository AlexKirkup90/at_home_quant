from __future__ import annotations

from typing import Literal

import numpy as np
import pandas as pd

MONTH_DAYS = 21
ANNUALIZATION_DAYS = 252


def _period_return(series: pd.Series, months: int) -> float:
    prices = series.dropna()
    if prices.empty:
        return float("nan")
    lookback = months * MONTH_DAYS
    if len(prices) <= lookback:
        return float("nan")
    end_price = prices.iloc[-1]
    start_price = prices.iloc[-(lookback + 1)]
    if start_price == 0:
        return float("nan")
    return float(end_price / start_price - 1)


def momentum_6m(series: pd.Series) -> float:
    return _period_return(series, 6)


def momentum_12m(series: pd.Series) -> float:
    return _period_return(series, 12)


def realized_vol(series: pd.Series, window_months: int = 12) -> float:
    prices = series.dropna()
    if prices.empty:
        return float("nan")
    window = window_months * MONTH_DAYS
    if len(prices) < 2:
        return float("nan")
    returns = prices.pct_change().dropna()
    if len(returns) < 2:
        return float("nan")
    if len(returns) > window:
        returns = returns.iloc[-window:]
    return float(returns.std(ddof=0) * np.sqrt(ANNUALIZATION_DAYS))


def stability_proxy(series: pd.Series, mode: Literal["synthetic", "fundamental"] = "synthetic") -> float:
    if mode == "fundamental":
        return float("nan")
    vol = realized_vol(series, window_months=12)
    if np.isnan(vol):
        return float("nan")
    return 1.0 / (1.0 + vol)


def _hashed_value(ticker: str, low: float, high: float) -> float:
    numeric = sum(ord(c) for c in ticker)
    fraction = (numeric % 10000) / 10000
    return low + fraction * (high - low)


def value_proxy(ticker: str, mode: Literal["synthetic", "fundamental"] = "synthetic") -> float:
    if mode == "fundamental":
        return float("nan")
    return _hashed_value(ticker, 0.015, 0.06)


def shareholder_yield_proxy(ticker: str, mode: Literal["synthetic", "fundamental"] = "synthetic") -> float:
    if mode == "fundamental":
        return float("nan")
    return _hashed_value(ticker[::-1], 0.0, 0.05)


__all__ = [
    "momentum_6m",
    "momentum_12m",
    "realized_vol",
    "stability_proxy",
    "value_proxy",
    "shareholder_yield_proxy",
]
