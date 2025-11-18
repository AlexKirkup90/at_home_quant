from __future__ import annotations

import math

from at_home_quant.regime.models import TrendSignal


def compute_composite_score(
    trend_signal: TrendSignal,
    momentum_rank: int,
    realized_vol: float,
    drawdown: float,
    yield_curve: float | None = None,
    vol_reference: float = 0.2,
) -> float:
    score = 0.0

    if trend_signal.is_bullish:
        score += 30.0

    score += 30.0 * (4 - momentum_rank) / 3.0

    if not math.isnan(realized_vol) and realized_vol > 0:
        vol_score = 20.0 * min(1.0, vol_reference / realized_vol)
        score += vol_score

    if yield_curve is not None and yield_curve > 0:
        score += 10.0

    if not math.isnan(drawdown):
        if drawdown < -0.10:
            score -= 40.0
        elif drawdown < 0:
            score += 10.0 * (drawdown / -0.10)

    score = max(0.0, min(100.0, score))
    return score


def equity_exposure_from_score(score: float) -> tuple[float, float]:
    if score >= 80:
        return (0.9, 1.0)
    if 60 <= score < 80:
        return (0.7, 0.9)
    if 40 <= score < 60:
        return (0.4, 0.7)
    return (0.0, 0.3)


__all__ = ["compute_composite_score", "equity_exposure_from_score"]
