from __future__ import annotations

import datetime
from typing import List

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import UNIVERSE_BENCHMARK_SYMBOL, Universe
from at_home_quant.db.models import PriceDaily, Ticker
from at_home_quant.db.session import get_session
from at_home_quant.regime.models import RegimeDecision, UniverseScore
from at_home_quant.regime.scoring import compute_composite_score, equity_exposure_from_score
from at_home_quant.regime.signals import (
    compute_drawdown,
    compute_momentum,
    compute_realized_vol,
    compute_trend,
    rank_momentum,
)


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


def _compute_scores(session: Session, as_of_date: datetime.date) -> list[UniverseScore]:
    trend_data: dict[Universe, tuple] = {}
    momentum_dict: dict[str, tuple[float, float]] = {}
    volatility: dict[Universe, float] = {}
    drawdowns: dict[Universe, float] = {}

    for universe, symbol in UNIVERSE_BENCHMARK_SYMBOL.items():
        series = _load_price_series(session, symbol, as_of_date)
        if series.empty:
            raise ValueError(f"No price history for {symbol} up to {as_of_date}")
        trend_signal = compute_trend(series)
        mom_6m = compute_momentum(series, 6)
        mom_12m = compute_momentum(series, 12)
        vol = compute_realized_vol(series)
        dd = compute_drawdown(series)

        trend_data[universe] = (trend_signal, mom_6m, mom_12m)
        momentum_dict[universe.value] = (mom_6m, mom_12m)
        volatility[universe] = vol
        drawdowns[universe] = dd

    momentum_ranks = rank_momentum(momentum_dict)

    scores: list[UniverseScore] = []
    for universe, (trend_signal, mom_6m, mom_12m) in trend_data.items():
        rank = momentum_ranks.get(universe.value, len(momentum_ranks))
        comp_score = compute_composite_score(
            trend_signal=trend_signal,
            momentum_rank=rank,
            realized_vol=volatility[universe],
            drawdown=drawdowns[universe],
            yield_curve=None,
        )
        exposure_min, exposure_max = equity_exposure_from_score(comp_score)
        scores.append(
            UniverseScore(
                as_of_date=as_of_date,
                universe_name=universe.value,
                composite_score=comp_score,
                trend=trend_signal.total_return_12m,
                momentum_6m=mom_6m,
                momentum_12m=mom_12m,
                momentum_rank=rank,
                realized_vol=volatility[universe],
                drawdown=drawdowns[universe],
                suggested_equity_min=exposure_min,
                suggested_equity_max=exposure_max,
            )
        )
    return scores


def get_universe_scores(as_of_date: datetime.date, session: Session | None = None) -> list[UniverseScore]:
    if session is not None:
        return _compute_scores(session, as_of_date)

    with get_session() as session_obj:
        return _compute_scores(session_obj, as_of_date)


def get_current_regime(as_of_date: datetime.date, session: Session | None = None) -> RegimeDecision:
    scores = get_universe_scores(as_of_date, session=session)
    if not scores:
        raise ValueError("No universe scores available")
    best = max(scores, key=lambda s: s.composite_score)
    return RegimeDecision(
        as_of_date=as_of_date,
        best_universe=best.universe_name,
        best_universe_score=best.composite_score,
        all_universe_scores=scores,
    )


__all__ = ["get_universe_scores", "get_current_regime"]
