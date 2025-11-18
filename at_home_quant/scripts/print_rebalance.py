import argparse
import datetime
from typing import List

from at_home_quant.portfolio.models import RebalanceInstruction
from at_home_quant.portfolio.service import build_monthly_portfolio, compute_rebalance
from at_home_quant.regime.service import get_current_regime


def _format_instructions(instructions: List[RebalanceInstruction]) -> str:
    lines = [
        f"{instr.ticker:8s} | {instr.action:4s} | {instr.current_weight:6.3f} -> "
        f"{instr.target_weight:6.3f} ({instr.delta:+6.3f})"
        for instr in instructions
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print monthly rebalance plan")
    parser.add_argument("--as-of", dest="as_of", required=True, help="As-of date YYYY-MM-DD")
    parser.add_argument("--threshold", type=float, default=0.005, help="Rebalance threshold")
    args = parser.parse_args()

    as_of_date = datetime.date.fromisoformat(args.as_of)

    regime = get_current_regime(as_of_date)
    print(f"Regime best universe: {regime.best_universe} (score={regime.best_universe_score:.2f})")

    portfolio = build_monthly_portfolio(as_of_date)
    print("\nTarget portfolio:")
    for pos in portfolio.positions:
        print(f" - {pos.ticker}: {pos.weight:.3f} ({pos.asset_type})")
    print(f"Equity exposure: {portfolio.equity_exposure:.3f}")
    print(f"Defensive exposure: {portfolio.defensive_exposure:.3f}")

    try:
        instructions = compute_rebalance(as_of_date, threshold=args.threshold)
        print("\nRebalance instructions:")
        print(_format_instructions(instructions))
    except ValueError as exc:
        print(f"\nRebalance instructions unavailable: {exc}")


if __name__ == "__main__":
    main()
