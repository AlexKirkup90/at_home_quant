import argparse
import datetime

from at_home_quant.selection.service import rank_universe


def main() -> None:
    parser = argparse.ArgumentParser(description="Print ranked equities for a universe")
    parser.add_argument("--universe", dest="universe", required=True, help="Universe name (e.g., NASDAQ100)")
    parser.add_argument("--as-of", dest="as_of", required=False, help="As-of date YYYY-MM-DD")
    parser.add_argument("--top-n", dest="top_n", type=int, default=15, help="Number of names to display")
    args = parser.parse_args()

    if args.as_of:
        as_of_date = datetime.datetime.strptime(args.as_of, "%Y-%m-%d").date()
    else:
        as_of_date = datetime.date.today()

    scores = rank_universe(args.universe, as_of_date, top_n=args.top_n)
    if not scores:
        print("No scores available for the requested universe/date")
        return

    print(f"Top {len(scores)} names for {args.universe} as of {as_of_date}:")
    header = f"{'Ticker':<8} {'Composite':>10} {'M6':>8} {'M12':>8} {'Stab':>8} {'Vol':>8} {'Value':>8} {'SH Yield':>10}"
    print(header)
    print("-" * len(header))
    for score in scores:
        print(
            f"{score.ticker:<8} {score.composite_score:>10.3f} "
            f"{score.momentum_6m:>8.3f} {score.momentum_12m:>8.3f} {score.stability:>8.3f} "
            f"{score.volatility:>8.3f} {score.value:>8.3f} {score.shareholder_yield:>10.3f}"
        )


if __name__ == "__main__":
    main()
