from __future__ import annotations

import argparse
import datetime

from at_home_quant.advisor.service import generate_weekly_recommendation
from at_home_quant.db.models import PriceDaily
from at_home_quant.db.session import get_session
from at_home_quant.etl.daily_update import run_daily_update


def _latest_price_date() -> datetime.date | None:
    from sqlalchemy import func, select

    with get_session() as session:
        return session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run automated weekly advisor backend cycle.")
    parser.add_argument("--as-of", help="Optional as-of date YYYY-MM-DD. Defaults to latest price date.")
    parser.add_argument("--top-n", type=int, default=15)
    parser.add_argument("--threshold", type=float, default=0.005)
    args = parser.parse_args()

    print("Step 1/2: Syncing data...")
    run_daily_update()

    if args.as_of:
        as_of_date = datetime.date.fromisoformat(args.as_of)
    else:
        as_of_date = _latest_price_date() or datetime.date.today()

    print("Step 3/3: Generating weekly recommendation...")
    report = generate_weekly_recommendation(
        as_of_date=as_of_date,
        top_n=args.top_n,
        threshold=args.threshold,
    )
    print(
        f"Done. batch_id={report.batch_id} as_of={report.as_of_date} "
        f"best_universe={report.best_universe} recommendations={len(report.recommendations)}"
    )


if __name__ == "__main__":
    main()
