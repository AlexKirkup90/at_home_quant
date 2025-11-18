from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable

import pandas as pd

from at_home_quant.regime.models import TrendSignal

TRADING_DAYS_PER_MONTH = 21
TRADING_DAYS_PER_YEAR = 252


def compute_trend(price_series: pd.Series) -> TrendSignal:
    if price_series.empty:
        raise ValueError("Price series is empty; cannot compute trend")
    price_series = price_series.sort_index()
    lookback_days = TRADING_DAYS_PER_YEAR
    if len(price_series) <= lookback_days:
        raise ValueError("Insufficient history for 12m trend computation")
    current_price = price_series.iloc[-1]
    past_price = price_series.iloc[-lookback_days - 1]
    total_return = (current_price / past_price) - 1
    sma_window = TRADING_DAYS_PER_MONTH * 10
    if len(price_series) < sma_window:
        sma_window = len(price_series)
    sma = price_series.tail(sma_window).mean()
    return TrendSignal(total_return_12m=total_return, price_above_sma_10m=current_price > sma)


def compute_momentum(price_series: pd.Series, window_months: int) -> float:
    if price_series.empty:
        return math.nan
    price_series = price_series.sort_index()
    window = TRADING_DAYS_PER_MONTH * window_months
    if len(price_series) <= window:
        return math.nan
    return (price_series.iloc[-1] / price_series.iloc[-window - 1]) - 1


def compute_realized_vol(price_series: pd.Series, window_days: int = 63) -> float:
    if price_series.empty or len(price_series) <= 1:
        return math.nan
    returns = price_series.sort_index().pct_change().dropna()
    if returns.empty:
        return math.nan
    if len(returns) < window_days:
        window_days = len(returns)
    return float(returns.tail(window_days).std() * math.sqrt(TRADING_DAYS_PER_YEAR))


def compute_drawdown(price_series: pd.Series) -> float:
    if price_series.empty:
        return math.nan
    price_series = price_series.sort_index()
    running_max = price_series.cummax()
    drawdowns = price_series / running_max - 1.0
    return float(drawdowns.iloc[-1])


def rank_momentum(momentum: Dict[str, tuple[float, float]]) -> Dict[str, int]:
    scores: Dict[str, float] = {}
    for key, (m6, m12) in momentum.items():
        components = [val for val in (m6, m12) if not math.isnan(val)]
        if not components:
            scores[key] = -math.inf
        else:
            scores[key] = sum(components) / len(components)
    sorted_keys = sorted(scores, key=lambda k: scores[k], reverse=True)
    ranks: Dict[str, int] = {}
    for idx, key in enumerate(sorted_keys, start=1):
        ranks[key] = idx
    return ranks


__all__ = [
    "compute_trend",
    "compute_momentum",
    "compute_realized_vol",
    "compute_drawdown",
    "rank_momentum",
    "TrendSignal",
]
