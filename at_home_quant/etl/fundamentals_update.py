from __future__ import annotations

import datetime
import logging
import math
from typing import Iterable

import yfinance as yf
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from at_home_quant.db.models import FundamentalSnapshot, Ticker
from at_home_quant.db.session import get_session, init_db


def _value_score(info: dict) -> float | None:
    trailing_pe = info.get("trailingPE")
    forward_pe = info.get("forwardPE")
    price_to_book = info.get("priceToBook")
    components = []
    for value in [trailing_pe, forward_pe]:
        if value is None:
            continue
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric <= 0:
            continue
        components.append(1.0 / numeric)
    if price_to_book is not None:
        try:
            pb = float(price_to_book)
        except (TypeError, ValueError):
            pb = float("nan")
        if pb > 0 and not math.isnan(pb):
            components.append(1.0 / pb)
    if not components:
        return None
    raw = sum(components) / len(components)
    return max(0.0, min(1.0, raw * 12.0))


def _shareholder_yield_score(info: dict) -> float | None:
    dividend_yield = info.get("dividendYield")
    if dividend_yield is None:
        return None
    try:
        numeric = float(dividend_yield)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or numeric < 0:
        return None
    return max(0.0, min(1.0, numeric / 0.08))


def _fetch_symbol_fundamentals(symbol: str) -> tuple[float | None, float | None]:
    info = yf.Ticker(symbol).info or {}
    return _value_score(info), _shareholder_yield_score(info)


def _upsert_snapshot_rows(
    session: Session,
    as_of_date: datetime.date,
    records: list[dict],
) -> None:
    if not records:
        return
    stmt = sqlite_insert(FundamentalSnapshot).values(
        [
            {
                "ticker_id": record["ticker_id"],
                "as_of_date": as_of_date,
                "value_score": record["value_score"],
                "shareholder_yield_score": record["shareholder_yield_score"],
            }
            for record in records
        ]
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[FundamentalSnapshot.ticker_id, FundamentalSnapshot.as_of_date],
        set_={
            "value_score": stmt.excluded.value_score,
            "shareholder_yield_score": stmt.excluded.shareholder_yield_score,
        },
    )
    session.execute(stmt)


def run_fundamentals_update(
    as_of_date: datetime.date | None = None,
    symbols: Iterable[str] | None = None,
    session: Session | None = None,
    fail_on_error: bool = False,
) -> int:
    init_db()
    effective_date = as_of_date or datetime.date.today()

    def _run(session_obj: Session) -> int:
        if symbols is None:
            symbol_rows = session_obj.execute(
                select(Ticker.symbol, Ticker.id).where(Ticker.asset_type == "EQUITY").order_by(Ticker.symbol)
            ).all()
        else:
            symbol_rows = session_obj.execute(
                select(Ticker.symbol, Ticker.id).where(Ticker.symbol.in_(list(set(symbols))))
            ).all()

        upsert_records: list[dict] = []
        for symbol, ticker_id in symbol_rows:
            try:
                value_score, shareholder_score = _fetch_symbol_fundamentals(symbol)
            except Exception as exc:  # noqa: BLE001
                if fail_on_error:
                    raise
                logging.getLogger(__name__).warning(
                    "Skipping fundamentals for %s due to fetch error: %s",
                    symbol,
                    exc,
                )
                continue
            if value_score is None and shareholder_score is None:
                continue
            upsert_records.append(
                {
                    "ticker_id": ticker_id,
                    "value_score": value_score,
                    "shareholder_yield_score": shareholder_score,
                }
            )
        _upsert_snapshot_rows(session_obj, effective_date, upsert_records)
        return len(upsert_records)

    if session is not None:
        return _run(session)

    with get_session() as session_obj:
        return _run(session_obj)


if __name__ == "__main__":
    count = run_fundamentals_update()
    print(f"Fundamentals updated for {count} symbols.")
