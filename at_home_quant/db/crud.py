from __future__ import annotations

import datetime
from typing import Iterable, Mapping, Sequence

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import ALL_TICKERS, TickerInfo
from at_home_quant.db.models import PriceDaily, Ticker, UniverseMembership

_SQLITE_MAX_VARIABLES = 999
_PRICE_UPSERT_COLUMNS = 9
_PRICE_UPSERT_BATCH_SIZE = _SQLITE_MAX_VARIABLES // _PRICE_UPSERT_COLUMNS


def upsert_tickers(session: Session, tickers: Mapping[str, TickerInfo] | Iterable[TickerInfo]) -> None:
    if isinstance(tickers, Mapping):
        values = tickers.values()
    else:
        values = tickers
    for info in values:
        stmt = sqlite_insert(Ticker).values(
            symbol=info.symbol,
            name=info.name,
            asset_type=info.asset_type,
            universe=info.universe,
            currency=info.currency,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Ticker.symbol],
            set_={
                "name": info.name,
                "asset_type": info.asset_type,
                "universe": info.universe,
                "currency": info.currency,
            },
        )
        session.execute(stmt)
        if info.universe is not None:
            ticker_id = session.execute(
                select(Ticker.id).where(Ticker.symbol == info.symbol)
            ).scalar_one()
            membership_stmt = sqlite_insert(UniverseMembership).values(
                ticker_id=ticker_id,
                universe=info.universe,
                effective_from=info.membership_start,
                effective_to=info.membership_end,
            )
            membership_stmt = membership_stmt.on_conflict_do_update(
                index_elements=[
                    UniverseMembership.ticker_id,
                    UniverseMembership.universe,
                    UniverseMembership.effective_from,
                ],
                set_={"effective_to": info.membership_end},
            )
            session.execute(membership_stmt)


def _ticker_symbol_to_id(session: Session, symbols: Sequence[str]) -> dict[str, int]:
    rows = session.execute(select(Ticker).where(Ticker.symbol.in_(symbols))).scalars().all()
    return {row.symbol: row.id for row in rows}


def upsert_prices(session: Session, price_df: pd.DataFrame) -> None:
    if price_df.empty:
        return

    required_cols = {"date", "symbol", "close"}
    missing_cols = required_cols - set(price_df.columns)
    if missing_cols:
        raise ValueError(f"Missing required price columns: {missing_cols}")

    symbols = sorted(price_df["symbol"].unique())
    symbol_to_id = _ticker_symbol_to_id(session, symbols)

    missing = [s for s in symbols if s not in symbol_to_id]
    if missing:
        # attempt to insert missing tickers from config
        subset = {s: ALL_TICKERS[s] for s in missing if s in ALL_TICKERS}
        upsert_tickers(session, subset)
        symbol_to_id.update(_ticker_symbol_to_id(session, missing))

    records = []
    for _, row in price_df.iterrows():
        ticker_id = symbol_to_id.get(row["symbol"])
        if ticker_id is None:
            continue
        close = row.get("close")
        adj_close = row.get("adj_close")
        if pd.isna(adj_close):
            adj_close = close
        if pd.isna(close):
            close = adj_close
        if pd.isna(close) or pd.isna(adj_close):
            continue
        records.append(
            {
                "ticker_id": ticker_id,
                "date": row["date"].date() if hasattr(row["date"], "date") else row["date"],
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": close,
                "adj_close": adj_close,
                "volume": row.get("volume"),
                "return_": row.get("return_"),
            }
        )

    if not records:
        return

    for index in range(0, len(records), _PRICE_UPSERT_BATCH_SIZE):
        batch = records[index:index + _PRICE_UPSERT_BATCH_SIZE]
        stmt = sqlite_insert(PriceDaily).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PriceDaily.ticker_id, PriceDaily.date],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "adj_close": stmt.excluded.adj_close,
                "volume": stmt.excluded.volume,
                "return_": stmt.excluded.return_,
            },
        )
        session.execute(stmt)


def latest_price_date(session: Session, ticker_id: int) -> datetime.date | None:
    stmt = select(PriceDaily.date).where(PriceDaily.ticker_id == ticker_id).order_by(PriceDaily.date.desc())
    result = session.execute(stmt).scalars().first()
    return result


def get_or_create_tickers(session: Session, tickers: Mapping[str, TickerInfo]) -> None:
    upsert_tickers(session, tickers)


__all__ = [
    "upsert_tickers",
    "upsert_prices",
    "latest_price_date",
    "get_or_create_tickers",
]
