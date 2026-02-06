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
    assert summary.gross_total_return == pytest.approx(summary.total_return)
    assert summary.total_transaction_cost == pytest.approx(0.0)
    assert summary.tracking_error == pytest.approx(0.0)
    assert summary.information_ratio is None
    assert summary.total_alpha == 0.02
    assert summary.avg_monthly_alpha == 0.01
    assert summary.positive_alpha_months == 2
    assert summary.alpha_hit_rate == pytest.approx(1.0)
    assert summary.avg_monthly_turnover == pytest.approx(0.0)


def test_summary_handles_single_month():
    series = [
        MonthlyPerformance(datetime.date(2024, 1, 31), datetime.date(2024, 2, 29), 0.05, "SPY", 0.03, 0.02)
    ]
    summary = compute_performance_summary(series)
    assert summary.volatility is None
    assert summary.sharpe is None
    assert summary.tracking_error is None
    assert summary.information_ratio is None
    assert summary.total_alpha == 0.02
    assert summary.avg_monthly_alpha == 0.02
    assert summary.positive_alpha_months == 1
    assert summary.alpha_hit_rate == pytest.approx(1.0)


def test_max_drawdown_simple_sequence():
    series = [
        MonthlyPerformance(datetime.date(2024, 1, 31), datetime.date(2024, 2, 29), 0.1, "SPY", 0.05, 0.05),
        MonthlyPerformance(datetime.date(2024, 2, 29), datetime.date(2024, 3, 31), -0.2, "SPY", -0.1, -0.1),
        MonthlyPerformance(datetime.date(2024, 3, 31), datetime.date(2024, 4, 30), 0.05, "SPY", 0.02, 0.03),
    ]
    summary = compute_performance_summary(series)
    assert summary.max_drawdown < 0
    assert summary.total_return == pytest.approx((1.1 * 0.8 * 1.05) - 1)


def test_summary_includes_cost_turnover_and_alpha_diagnostics():
    series = [
        MonthlyPerformance(
            datetime.date(2024, 1, 31),
            datetime.date(2024, 2, 29),
            portfolio_return=0.028,
            benchmark_name="QQQ",
            benchmark_return=0.018,
            alpha=0.01,
            portfolio_return_gross=0.03,
            transaction_cost=0.002,
            portfolio_turnover=0.50,
        ),
        MonthlyPerformance(
            datetime.date(2024, 2, 29),
            datetime.date(2024, 3, 31),
            portfolio_return=0.009,
            benchmark_name="QQQ",
            benchmark_return=0.01,
            alpha=-0.001,
            portfolio_return_gross=0.01,
            transaction_cost=0.001,
            portfolio_turnover=0.25,
        ),
    ]
    summary = compute_performance_summary(series)
    assert summary.gross_total_return == pytest.approx((1.03 * 1.01) - 1)
    assert summary.total_return == pytest.approx((1.028 * 1.009) - 1)
    assert summary.total_transaction_cost == pytest.approx(0.003)
    assert summary.avg_monthly_turnover == pytest.approx(0.375)
    assert summary.positive_alpha_months == 1
    assert summary.alpha_hit_rate == pytest.approx(0.5)
    assert summary.tracking_error is not None
    assert summary.information_ratio is not None
