import argparse
import datetime

from at_home_quant.regime.service import get_current_regime


def main() -> None:
    parser = argparse.ArgumentParser(description="Print current regime decision")
    parser.add_argument("--as-of", dest="as_of", required=False, help="As-of date YYYY-MM-DD")
    args = parser.parse_args()

    if args.as_of:
        as_of_date = datetime.datetime.strptime(args.as_of, "%Y-%m-%d").date()
    else:
        as_of_date = datetime.date.today()

    decision = get_current_regime(as_of_date)
    print(f"Regime decision for {decision.as_of_date}:")
    print(f"Best universe: {decision.best_universe} (score={decision.best_universe_score:.2f})")
    for score in decision.all_universe_scores:
        print(
            f" - {score.universe_name}: score={score.composite_score:.2f}, "
            f"trend={score.trend:.3f}, m6={score.momentum_6m:.3f}, m12={score.momentum_12m:.3f}, "
            f"vol={score.realized_vol:.3f}, drawdown={score.drawdown:.3f}, "
            f"equity_range={score.suggested_equity_min:.0%}-{score.suggested_equity_max:.0%}"
        )


if __name__ == "__main__":
    main()
