import datetime

from at_home_quant.portfolio.optimizer import (
    DEFAULT_MAX_POSITION,
    build_defensive_positions,
    build_equity_positions,
    suggest_exposures,
)
from at_home_quant.selection.models import StockFactorScores


def _mock_scores(count: int) -> list[StockFactorScores]:
    base = []
    for i in range(count):
        base.append(
            StockFactorScores(
                ticker=f"STK{i}",
                momentum_6m=0.1 + i,
                momentum_12m=0.2 + i,
                stability=0.3,
                volatility=0.1,
                value=0.02,
                shareholder_yield=0.01,
                composite_score=0.5 + 0.1 * i,
            )
        )
    return base


def test_equity_weights_respect_exposure_and_cap():
    scores = _mock_scores(5)
    positions = build_equity_positions(scores, equity_exposure=0.6, max_position=0.2)
    total = sum(p.weight for p in positions)
    assert abs(total - 0.6) < 1e-6
    assert all(p.weight <= 0.2 + 1e-6 for p in positions)


def test_defensive_and_exposure_splits():
    high_equity, low_defensive = suggest_exposures(90, 0.4, 0.8)
    assert low_defensive <= 0.1
    assert abs(high_equity + low_defensive - 1.0) < 1e-6

    cautious_equity, defensive = suggest_exposures(20, 0.4, 0.8)
    assert defensive >= 0.6
    assert abs(cautious_equity + defensive - 1.0) < 1e-6

    defensive_positions = build_defensive_positions(defensive)
    gold = next(p for p in defensive_positions if p.asset_type == "gold")
    cash = next(p for p in defensive_positions if p.asset_type == "cash")
    assert abs(gold.weight - defensive * 0.4) < 1e-6
    assert abs(cash.weight - defensive * 0.6) < 1e-6
