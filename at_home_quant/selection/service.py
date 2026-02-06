from __future__ import annotations

import datetime
from typing import List

import pandas as pd
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import Universe
from at_home_quant.db.models import FundamentalSnapshot, PriceDaily, Ticker, UniverseMembership
from at_home_quant.db.session import get_session
from at_home_quant.selection.factors import (
    momentum_12m,
    momentum_6m,
    realized_vol,
    shareholder_yield_proxy,
    stability_proxy,
    value_proxy,
)
from at_home_quant.selection.models import StockFactorScores
from at_home_quant.selection.ranking import DEFAULT_WEIGHTS, rank_stocks


FACTOR_COLUMNS = ["momentum", "stability", "low_volatility", "value", "shareholder_yield"]


def _load_price_series(session: Session, symbol: str, as_of_date: datetime.date) -> pd.Series:
    stmt = (
        select(PriceDaily.date, PriceDaily.adj_close)
        .join(Ticker, Ticker.id == PriceDaily.ticker_id)
        .where(Ticker.symbol == symbol, PriceDaily.date <= as_of_date)
        .order_by(PriceDaily.date)
    )
    rows = session.execute(stmt).all()
    if not rows:
        return pd.Series(dtype=float)
    df = pd.DataFrame(rows, columns=["date", "adj_close"]).set_index("date")
    return df["adj_close"]


def _compute_factors_for_ticker(symbol: str, series: pd.Series) -> dict:
    mom6 = momentum_6m(series)
    mom12 = momentum_12m(series)
    momentum = float(pd.Series([mom6, mom12]).mean())
    vol = realized_vol(series)
    stability = stability_proxy(series)
    value = value_proxy(symbol)
    shareholder = shareholder_yield_proxy(symbol)
    return {
        "ticker": symbol,
        "momentum_6m": mom6,
        "momentum_12m": mom12,
        "momentum": momentum,
        "stability": stability,
        "volatility": vol,
        "low_volatility": -vol if pd.notna(vol) else float("nan"),
        "value": value,
        "shareholder_yield": shareholder,
    }


def _point_in_time_fundamentals(
    session: Session,
    symbol: str,
    as_of_date: datetime.date,
) -> tuple[float, float]:
    row = session.execute(
        select(FundamentalSnapshot.value_score, FundamentalSnapshot.shareholder_yield_score)
        .join(Ticker, Ticker.id == FundamentalSnapshot.ticker_id)
        .where(
            Ticker.symbol == symbol,
            FundamentalSnapshot.as_of_date <= as_of_date,
        )
        .order_by(FundamentalSnapshot.as_of_date.desc())
    ).first()
    if row is None:
        return value_proxy(symbol), shareholder_yield_proxy(symbol)
    value_score, shareholder_score = row
    return (
        value_proxy(symbol) if value_score is None else float(value_score),
        shareholder_yield_proxy(symbol) if shareholder_score is None else float(shareholder_score),
    )


def _compute_universe_factors(session: Session, universe: Universe, as_of_date: datetime.date) -> pd.DataFrame:
    try:
        active_members = session.execute(
            select(Ticker.symbol)
            .join(UniverseMembership, UniverseMembership.ticker_id == Ticker.id)
            .where(
                UniverseMembership.universe == universe,
                UniverseMembership.effective_from <= as_of_date,
                (UniverseMembership.effective_to.is_(None) | (UniverseMembership.effective_to >= as_of_date)),
            )
            .order_by(Ticker.symbol)
        ).scalars().all()
        if active_members:
            tickers = active_members
        else:
            membership_exists = session.execute(
                select(UniverseMembership.id).where(UniverseMembership.universe == universe).limit(1)
            ).scalar_one_or_none()
            if membership_exists is None:
                tickers = session.execute(
                    select(Ticker.symbol).where(Ticker.universe == universe).order_by(Ticker.symbol)
                ).scalars().all()
            else:
                tickers = []
    except SQLAlchemyError:
        tickers = session.execute(
            select(Ticker.symbol).where(Ticker.universe == universe).order_by(Ticker.symbol)
        ).scalars().all()
    factors: list[dict] = []
    for symbol in tickers:
        series = _load_price_series(session, symbol, as_of_date)
        if series.empty:
            continue
        factor = _compute_factors_for_ticker(symbol, series)
        value_score, shareholder_score = _point_in_time_fundamentals(session, symbol, as_of_date)
        factor["value"] = value_score
        factor["shareholder_yield"] = shareholder_score
        factors.append(factor)
    return pd.DataFrame(factors)


def _rank(session: Session, universe: Universe, as_of_date: datetime.date, top_n: int) -> list[StockFactorScores]:
    factor_df = _compute_universe_factors(session, universe, as_of_date)
    if factor_df.empty:
        return []
    ranked = rank_stocks(factor_df[FACTOR_COLUMNS + ["ticker", "momentum_6m", "momentum_12m", "volatility"]], weights=DEFAULT_WEIGHTS)
    selected = ranked.head(top_n)
    results: List[StockFactorScores] = []
    for _, row in selected.iterrows():
        results.append(
            StockFactorScores(
                ticker=row["ticker"],
                momentum_6m=float(row["momentum_6m"]),
                momentum_12m=float(row["momentum_12m"]),
                stability=float(row["stability"]),
                volatility=float(row["volatility"]),
                value=float(row["value"]),
                shareholder_yield=float(row["shareholder_yield"]),
                composite_score=float(row["composite_score"]),
            )
        )
    return results


def rank_universe(
    universe_name: str, as_of_date: datetime.date, top_n: int = 15, session: Session | None = None
) -> list[StockFactorScores]:
    universe = Universe[universe_name]
    if session is not None:
        return _rank(session, universe, as_of_date, top_n)

    with get_session() as session_obj:
        return _rank(session_obj, universe, as_of_date, top_n)


__all__ = ["rank_universe"]
