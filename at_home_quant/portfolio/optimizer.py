from __future__ import annotations

import math
from typing import Iterable, List

from at_home_quant.portfolio.models import TargetPosition
from at_home_quant.selection.models import StockFactorScores


DEFAULT_MAX_POSITION = 0.15


def _softmax(values: Iterable[float]) -> list[float]:
    values_list = list(values)
    if not values_list:
        return []
    exps = [math.exp(v) for v in values_list]
    total = sum(exps)
    if total == 0:
        return [1 / len(exps)] * len(exps)
    return [v / total for v in exps]


def _normalized_linear(values: Iterable[float]) -> list[float]:
    values_list = list(values)
    if not values_list:
        return []
    min_val = min(values_list)
    shifted = [v - min_val + 1e-6 for v in values_list]
    total = sum(shifted)
    if total == 0:
        return [1 / len(shifted)] * len(shifted)
    return [v / total for v in shifted]


def _apply_max_position(weights: list[float], max_position: float) -> list[float]:
    if not weights:
        return []
    clipped = [min(w, max_position) for w in weights]
    total = sum(clipped)
    if total == 0:
        return [1 / len(weights)] * len(weights)
    return [w / total for w in clipped]


def suggest_exposures(regime_score: float, equity_min: float, equity_max: float) -> tuple[float, float]:
    base_equity = (equity_min + equity_max) / 2
    defensive = max(0.0, 1 - base_equity)
    if regime_score > 80:
        defensive = min(defensive, 0.1)
    elif regime_score < 40:
        defensive = max(defensive, 0.6)
    equity_exposure = 1 - defensive
    return equity_exposure, defensive


def build_equity_positions(
    ranked_stocks: List[StockFactorScores],
    equity_exposure: float,
    weighting_method: str = "softmax",
    max_position: float = DEFAULT_MAX_POSITION,
) -> list[TargetPosition]:
    if not ranked_stocks or equity_exposure <= 0:
        return []

    scores = [s.composite_score for s in ranked_stocks]
    if weighting_method == "linear":
        base_weights = _normalized_linear(scores)
    else:
        base_weights = _softmax(scores)

    constrained = _apply_max_position(base_weights, max_position)
    scaled = [w * equity_exposure for w in constrained]
    positions = [
        TargetPosition(ticker=stock.ticker, weight=weight, asset_type="equity")
        for stock, weight in zip(ranked_stocks, scaled)
    ]
    return positions


def build_defensive_positions(
    defensive_exposure: float, gold_ticker: str = "GLD", cash_ticker: str = "BIL"
) -> list[TargetPosition]:
    if defensive_exposure <= 0:
        return []
    gold_weight = defensive_exposure * 0.4
    cash_weight = defensive_exposure * 0.6
    return [
        TargetPosition(ticker=gold_ticker, weight=gold_weight, asset_type="gold"),
        TargetPosition(ticker=cash_ticker, weight=cash_weight, asset_type="cash"),
    ]


__all__ = [
    "build_equity_positions",
    "build_defensive_positions",
    "suggest_exposures",
    "DEFAULT_MAX_POSITION",
]
