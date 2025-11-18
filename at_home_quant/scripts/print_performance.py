from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from typing import List

from at_home_quant.performance.service import get_monthly_performance, get_performance_summary


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value*100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Print monthly performance and summary stats")
    parser.add_argument("--csv", dest="csv_path", help="Optional path to export monthly performance as CSV")
    args = parser.parse_args()

    monthly = get_monthly_performance()
    summary = get_performance_summary()

    print("Monthly Performance")
    print(
        f"{'Start':<12} {'End':<12} {'Port Ret':>10} {'Benchmark':>12} {'Bench Ret':>10} {'Alpha':>10}"
    )
    for item in monthly:
        highlight = "" if abs(item.alpha) < 0.02 else ("+" if item.alpha > 0 else "-")
        print(
            f"{item.period_start} {item.period_end} "
            f"{_format_pct(item.portfolio_return):>10} {item.benchmark_name:>12} "
            f"{_format_pct(item.benchmark_return):>10} {highlight}{_format_pct(item.alpha):>9}"
        )

    print("\nSummary")
    print(f"Start Date:       {summary.start_date}")
    print(f"End Date:         {summary.end_date}")
    print(f"Total Return:     {_format_pct(summary.total_return)}")
    print(f"CAGR:             {_format_pct(summary.cagr)}")
    print(f"Volatility:       {_format_pct(summary.volatility) if summary.volatility is not None else 'N/A'}")
    print(f"Max Drawdown:     {_format_pct(summary.max_drawdown)}")
    print(f"Sharpe:           {summary.sharpe:.2f}" if summary.sharpe is not None else "Sharpe:           N/A")
    print(f"Total Alpha:      {_format_pct(summary.total_alpha)}")
    print(f"Avg Monthly Alpha:{_format_pct(summary.avg_monthly_alpha)}")

    if args.csv_path:
        with open(args.csv_path, "w", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "period_start",
                    "period_end",
                    "portfolio_return",
                    "benchmark_name",
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
