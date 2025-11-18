import pandas as pd

from at_home_quant.regime.signals import (
    compute_drawdown,
    compute_momentum,
    compute_realized_vol,
    compute_trend,
)


def _build_series(start: float, step: float, periods: int) -> pd.Series:
    dates = pd.bdate_range(start="2020-01-01", periods=periods)
    values = [start + i * step for i in range(periods)]
    return pd.Series(values, index=dates)


def test_trend_signal_positive_on_rising_series():
    series = _build_series(100, 0.5, 300)
    trend = compute_trend(series)
    assert trend.total_return_12m > 0
    assert trend.price_above_sma_10m


def test_trend_signal_negative_on_falling_series():
    series = _build_series(200, -0.5, 300)
    trend = compute_trend(series)
    assert trend.total_return_12m < 0
    drawdown = compute_drawdown(series)
    assert drawdown < 0


def test_momentum_and_volatility_behaviour():
    series = _build_series(100, 0.2, 400)
    mom_6m = compute_momentum(series, 6)
    mom_12m = compute_momentum(series, 12)
    vol = compute_realized_vol(series)
    assert mom_12m > mom_6m > 0
    assert vol > 0
