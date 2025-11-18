import datetime

import numpy as np
import pandas as pd
import pytest

from at_home_quant.selection import factors


def _build_price_series(days: int, daily_return: float) -> pd.Series:
    dates = pd.bdate_range(end=datetime.date.today(), periods=days)
    prices = 100 * (1 + daily_return) ** np.arange(days)
    return pd.Series(prices, index=dates)


def test_momentum_and_volatility():
    series = _build_price_series(300, 0.001)
    mom6 = factors.momentum_6m(series)
    mom12 = factors.momentum_12m(series)
    assert mom6 > 0
    assert mom12 > mom6 / 2

    vol = factors.realized_vol(series)
    assert vol == pytest.approx(0.0, abs=1e-12)  # constant daily return => zero volatility


def test_stability_and_synthetic_yields_are_deterministic():
    series = _build_price_series(260, 0.0)
    stability = factors.stability_proxy(series)
    assert stability == 1.0

    val1 = factors.value_proxy("AAPL")
    val2 = factors.value_proxy("AAPL")
    sh1 = factors.shareholder_yield_proxy("MSFT")
    sh2 = factors.shareholder_yield_proxy("MSFT")
    assert val1 == val2
    assert sh1 == sh2
    assert 0 <= sh1 <= 0.05
    assert 0.015 <= val1 <= 0.06
