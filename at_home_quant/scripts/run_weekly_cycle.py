from __future__ import annotations

import argparse
import datetime

from at_home_quant.backend.service import run_backend_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated weekly advisor backend cycle.")
    parser.add_argument("--as-of", help="Optional as-of date YYYY-MM-DD. Defaults to latest price date.")
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--threshold", type=float, default=0.005)
    parser.add_argument("--retries", type=int, default=2)
    args = parser.parse_args()

    as_of_date = datetime.date.fromisoformat(args.as_of) if args.as_of else None
    result = run_backend_pipeline(
        as_of_date=as_of_date,
        include_weekly_recommendation=True,
        retries=args.retries,
        top_n=args.top_n,
        threshold=args.threshold,
    )
    print(
        f"Done. run_id={result.run_id} status={result.status} as_of={result.as_of_date} "
        f"snapshot={result.data_snapshot_hash} batch={result.recommendation_batch_id}"
    )


if __name__ == "__main__":
    main()
