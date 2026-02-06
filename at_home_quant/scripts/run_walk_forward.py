from __future__ import annotations

import argparse
import datetime

from at_home_quant.research.service import run_walk_forward_experiment


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run walk-forward experiment and persist model report")
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
    parser.add_argument("--train-months", type=int, default=36)
    parser.add_argument("--validation-months", type=int, default=12)
    parser.add_argument("--holdout-months", type=int, default=12)
    args = parser.parse_args()

    end_date = datetime.date.fromisoformat(args.end) if args.end else None
    result = run_walk_forward_experiment(
        end_date=end_date,
        top_n=args.top_n,
        train_months=args.train_months,
        validation_months=args.validation_months,
        holdout_months=args.holdout_months,
        benchmark_timing=args.benchmark_timing,
        transaction_cost_bps=args.transaction_cost_bps,
        slippage_bps=args.slippage_bps,
        max_position=args.max_position,
        max_turnover=args.max_turnover,
    )

    print(f"Experiment ID:    {result.experiment_id}")
    print(f"Run Type:         {result.run_type}")
    print(f"As-Of Date:       {result.as_of_date.isoformat()}")
    print(f"Data Snapshot:    {result.feature_snapshot_hash}")
    print(f"Linked Backtest:  {result.linked_run_id}")
    print(f"Total Return:     {_format_pct(result.metrics.get('total_return'))}")
    print(f"CAGR:             {_format_pct(result.metrics.get('cagr'))}")
    print(f"Volatility:       {_format_pct(result.metrics.get('volatility'))}")
    print(f"Max Drawdown:     {_format_pct(result.metrics.get('max_drawdown'))}")
    print(
        f"Information Ratio:{result.metrics['information_ratio']:.2f}"
        if result.metrics.get("information_ratio") is not None
        else "Information Ratio:N/A"
    )
    print(f"Challenger vs SPY:{_format_pct(result.challenger_comparison.get('outperformance_vs_spy'))}")


if __name__ == "__main__":
    main()
