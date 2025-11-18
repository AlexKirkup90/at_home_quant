import datetime
from typing import Iterable, Mapping, Sequence

import pandas as pd
from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import ALL_TICKERS, TickerInfo
from at_home_quant.db.models import PriceDaily, Ticker


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


def _ticker_symbol_to_id(session: Session, symbols: Sequence[str]) -> dict[str, int]:
    rows = session.execute(select(Ticker).where(Ticker.symbol.in_(symbols))).scalars().all()
    return {row.symbol: row.id for row in rows}


def upsert_prices(session: Session, price_df: pd.DataFrame) -> None:
    if price_df.empty:
        return

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
        records.append(
            {
                "ticker_id": ticker_id,
                "date": row["date"].date() if hasattr(row["date"], "date") else row["date"],
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "adj_close": row.get("adj_close"),
                "volume": row.get("volume"),
                "return_": row.get("return_"),
            }
        )

    if not records:
        return

    stmt = sqlite_insert(PriceDaily).values(records)
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
