from __future__ import annotations

import argparse
import datetime

from at_home_quant.backtest.service import run_walk_forward_backtest


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run walk-forward backtest and persist run artifacts")
    parser.add_argument("--start", help="Optional start date YYYY-MM-DD")
    parser.add_argument("--end", help="Optional end date YYYY-MM-DD")
    parser.add_argument("--top-n", type=int, default=15, help="Top N equities for each monthly rebalance")
    parser.add_argument(
        "--benchmark-timing",
        choices=["period_start", "period_end"],
        help="When benchmark universe is selected for each monthly period.",
    )
    parser.add_argument("--transaction-cost-bps", type=float, help="One-way transaction cost override.")
    parser.add_argument("--slippage-bps", type=float, help="One-way slippage override.")
    parser.add_argument("--max-position", type=float, help="Maximum single position weight override.")
    parser.add_argument("--max-turnover", type=float, help="Maximum monthly turnover override.")
    args = parser.parse_args()

    start_date = datetime.date.fromisoformat(args.start) if args.start else None
    end_date = datetime.date.fromisoformat(args.end) if args.end else None
    result = run_walk_forward_backtest(
        start_date=start_date,
        end_date=end_date,
        top_n=args.top_n,
        benchmark_timing=args.benchmark_timing,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
        max_position=args.max_position,
        max_turnover=args.max_turnover,
    )

    print(f"Run ID:           {result.run_id}")
    print(f"Created At:       {result.created_at.isoformat()}")
    print(f"Code Hash:        {result.code_hash or 'N/A'}")
    print(f"Data Snapshot:    {result.data_snapshot_hash}")
    print(f"Periods:          {len(result.monthly)}")
    print(f"Total Return:     {_format_pct(result.summary.total_return)}")
    print(f"CAGR:             {_format_pct(result.summary.cagr)}")
    print(f"Volatility:       {_format_pct(result.summary.volatility)}")
    print(f"Max Drawdown:     {_format_pct(result.summary.max_drawdown)}")
    print(f"Information Ratio:{result.summary.information_ratio:.2f}" if result.summary.information_ratio is not None else "Information Ratio:N/A")


if __name__ == "__main__":
    main()
