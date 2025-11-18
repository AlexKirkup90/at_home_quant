import datetime

import datetime

import pytest

from at_home_quant.performance.models import MonthlyPerformance
from at_home_quant.performance.stats import compute_performance_summary


def test_summary_basic_metrics():
    series = [
        MonthlyPerformance(datetime.date(2024, 1, 31), datetime.date(2024, 2, 29), 0.02, "QQQ", 0.01, 0.01),
        MonthlyPerformance(datetime.date(2024, 2, 29), datetime.date(2024, 3, 31), 0.01, "QQQ", 0.0, 0.01),
    ]
    summary = compute_performance_summary(series)
    assert summary.months == 2
    assert pytest.approx(summary.total_return) == (1.02 * 1.01 - 1)
    assert summary.cagr > 0
    assert summary.volatility is not None
    assert summary.max_drawdown <= 0
    assert summary.total_alpha == 0.02
    assert summary.avg_monthly_alpha == 0.01


def test_summary_handles_single_month():
    series = [
        MonthlyPerformance(datetime.date(2024, 1, 31), datetime.date(2024, 2, 29), 0.05, "SPY", 0.03, 0.02)
    ]
    summary = compute_performance_summary(series)
    assert summary.volatility is None
    assert summary.sharpe is None
    assert summary.total_alpha == 0.02
    assert summary.avg_monthly_alpha == 0.02


def test_max_drawdown_simple_sequence():
    series = [
        MonthlyPerformance(datetime.date(2024, 1, 31), datetime.date(2024, 2, 29), 0.1, "SPY", 0.05, 0.05),
        MonthlyPerformance(datetime.date(2024, 2, 29), datetime.date(2024, 3, 31), -0.2, "SPY", -0.1, -0.1),
        MonthlyPerformance(datetime.date(2024, 3, 31), datetime.date(2024, 4, 30), 0.05, "SPY", 0.02, 0.03),
    ]
    summary = compute_performance_summary(series)
    assert summary.max_drawdown < 0
    assert summary.total_return == pytest.approx((1.1 * 0.8 * 1.05) - 1)
