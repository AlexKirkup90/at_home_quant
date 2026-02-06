from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from typing import List

from at_home_quant.config.settings import get_settings
from at_home_quant.performance.service import get_monthly_performance, get_performance_summary


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value*100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Print monthly performance and summary stats")
    parser.add_argument("--csv", dest="csv_path", help="Optional path to export monthly performance as CSV")
    parser.add_argument(
        "--benchmark-timing",
        choices=["period_start", "period_end"],
        help="When to select benchmark universe for each period (defaults to configured setting).",
    )
    parser.add_argument(
        "--transaction-cost-bps",
        type=float,
        help="Override one-way transaction cost in basis points.",
    )
    parser.add_argument(
        "--slippage-bps",
        type=float,
        help="Override one-way slippage in basis points.",
    )
    args = parser.parse_args()

    settings = get_settings()
    benchmark_timing = args.benchmark_timing or settings.benchmark_selection_timing
    transaction_cost_bps = (
        settings.transaction_cost_bps if args.transaction_cost_bps is None else args.transaction_cost_bps
    )
    slippage_bps = settings.slippage_bps if args.slippage_bps is None else args.slippage_bps

    monthly = get_monthly_performance(
        benchmark_timing=benchmark_timing,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )
    summary = get_performance_summary(
        benchmark_timing=benchmark_timing,
        transaction_cost_bps=transaction_cost_bps,
        slippage_bps=slippage_bps,
    )

    print(
        f"Assumptions: benchmark_timing={benchmark_timing}, "
        f"transaction_cost_bps={transaction_cost_bps:.2f}, slippage_bps={slippage_bps:.2f}"
    )

    print("Monthly Performance")
    print(
        f"{'Start':<12} {'End':<12} {'Gross':>9} {'Turnover':>10} {'Cost':>8} {'Net':>9} "
        f"{'Benchmark':>10} {'Bench Ret':>10} {'Alpha':>10}"
    )
    for item in monthly:
        highlight = "" if abs(item.alpha) < 0.02 else ("+" if item.alpha > 0 else "-")
        gross_return = item.portfolio_return_gross if item.portfolio_return_gross is not None else item.portfolio_return
        print(
            f"{item.period_start} {item.period_end} "
            f"{_format_pct(gross_return):>9} "
            f"{_format_pct(item.portfolio_turnover):>10} "
            f"{_format_pct(item.transaction_cost):>8} "
            f"{_format_pct(item.portfolio_return):>9} {item.benchmark_name:>10} "
            f"{_format_pct(item.benchmark_return):>10} {highlight}{_format_pct(item.alpha):>9}"
        )

    print("\nSummary")
    print(f"Start Date:       {summary.start_date}")
    print(f"End Date:         {summary.end_date}")
    print(f"Gross Return:     {_format_pct(summary.gross_total_return)}")
    print(f"Total Return:     {_format_pct(summary.total_return)}")
    print(f"Total Cost Drag:  {_format_pct(summary.total_transaction_cost)}")
    print(f"CAGR:             {_format_pct(summary.cagr)}")
    print(f"Volatility:       {_format_pct(summary.volatility) if summary.volatility is not None else 'N/A'}")
    print(f"Max Drawdown:     {_format_pct(summary.max_drawdown)}")
    print(f"Sharpe:           {summary.sharpe:.2f}" if summary.sharpe is not None else "Sharpe:           N/A")
    print(
        f"Tracking Error:   {_format_pct(summary.tracking_error) if summary.tracking_error is not None else 'N/A'}"
    )
    print(
        f"Information Ratio:{summary.information_ratio:.2f}"
        if summary.information_ratio is not None
        else "Information Ratio:N/A"
    )
    print(f"Total Alpha:      {_format_pct(summary.total_alpha)}")
    print(f"Avg Monthly Alpha:{_format_pct(summary.avg_monthly_alpha)}")
    print(f"Alpha Hit Rate:   {summary.positive_alpha_months}/{summary.months} ({_format_pct(summary.alpha_hit_rate)})")
    print(f"Avg Turnover:     {_format_pct(summary.avg_monthly_turnover)}")

    if args.csv_path:
        with open(args.csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "period_start",
                    "period_end",
                    "portfolio_return",
                    "portfolio_return_gross",
                    "transaction_cost",
                    "portfolio_turnover",
                    "benchmark_name",
                    "benchmark_timing",
                    "benchmark_selection_date",
                    "benchmark_return",
                    "alpha",
                ],
            )
            writer.writeheader()
            for item in monthly:
                writer.writerow(asdict(item))
        print(f"\nMonthly performance exported to {args.csv_path}")


if __name__ == "__main__":
    main()
