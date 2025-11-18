from at_home_quant.regime.models import TrendSignal
from at_home_quant.regime.scoring import compute_composite_score, equity_exposure_from_score


def test_composite_score_ranking_behaviour():
    bullish = TrendSignal(total_return_12m=0.15, price_above_sma_10m=True)
    bearish = TrendSignal(total_return_12m=-0.05, price_above_sma_10m=False)

    score_rank1 = compute_composite_score(bullish, momentum_rank=1, realized_vol=0.15, drawdown=-0.02)
    score_rank3 = compute_composite_score(bearish, momentum_rank=3, realized_vol=0.5, drawdown=-0.2)

    assert score_rank1 > score_rank3
    assert score_rank1 > 0


def test_equity_exposure_bands():
    assert equity_exposure_from_score(85) == (0.9, 1.0)
    assert equity_exposure_from_score(75) == (0.7, 0.9)
    assert equity_exposure_from_score(50) == (0.4, 0.7)
    assert equity_exposure_from_score(20) == (0.0, 0.3)
