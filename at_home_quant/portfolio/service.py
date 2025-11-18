from __future__ import annotations

import datetime
import json
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.db.models import Base, PortfolioSnapshot
from at_home_quant.db.session import get_session
from at_home_quant.portfolio.models import RebalanceInstruction, TargetPortfolio, TargetPosition
from at_home_quant.portfolio.optimizer import (
    DEFAULT_MAX_POSITION,
    build_defensive_positions,
    build_equity_positions,
    suggest_exposures,
)
from at_home_quant.portfolio.rebalance import diff_portfolios
from at_home_quant.regime.service import get_current_regime
from at_home_quant.selection.service import rank_universe


def _serialize_positions(positions: List[TargetPosition]) -> list[dict]:
    return [
        {"ticker": p.ticker, "weight": p.weight, "asset_type": p.asset_type}
        for p in positions
    ]


def _deserialize_positions(data: list[dict]) -> list[TargetPosition]:
    return [TargetPosition(**item) for item in data]


def _save_snapshot(session: Session, portfolio: TargetPortfolio) -> None:
    Base.metadata.create_all(bind=session.bind)
    existing = session.execute(
        select(PortfolioSnapshot).where(PortfolioSnapshot.as_of_date == portfolio.as_of_date)
    ).scalar_one_or_none()
    if existing:
        session.delete(existing)
        session.flush()
    snapshot = PortfolioSnapshot(
        as_of_date=portfolio.as_of_date,
        universe_name=portfolio.universe_name,
        equity_exposure=portfolio.equity_exposure,
        defensive_exposure=portfolio.defensive_exposure,
        positions_json=json.dumps(_serialize_positions(portfolio.positions)),
    )
    session.add(snapshot)
    session.commit()


def _load_last_snapshot(session: Session) -> TargetPortfolio | None:
    row = session.execute(
        select(PortfolioSnapshot).order_by(PortfolioSnapshot.as_of_date.desc())
    ).scalar_one_or_none()
    if row is None:
        return None
    positions = _deserialize_positions(json.loads(row.positions_json))
    portfolio = TargetPortfolio(
        as_of_date=row.as_of_date,
        positions=positions,
        universe_name=row.universe_name,
        equity_exposure=row.equity_exposure,
        defensive_exposure=row.defensive_exposure,
    )
    return portfolio


def build_monthly_portfolio(
    as_of_date: datetime.date,
    top_n: int = 15,
    max_position: float = DEFAULT_MAX_POSITION,
    weighting_method: str = "softmax",
    session: Session | None = None,
) -> TargetPortfolio:
    def _build(session_obj: Session) -> TargetPortfolio:
        regime = get_current_regime(as_of_date, session=session_obj)
        best_universe = regime.best_universe
        best_score = next(
            (s for s in regime.all_universe_scores if s.universe_name == best_universe), None
        )
        if best_score is None:
            raise ValueError("Unable to locate best universe score")

        equity_exposure, defensive_exposure = suggest_exposures(
            regime.best_universe_score, best_score.suggested_equity_min, best_score.suggested_equity_max
        )

        ranked = rank_universe(best_universe, as_of_date, top_n=top_n, session=session_obj)
        if not ranked:
            equity_exposure = 0.0
            defensive_exposure = 1.0

        equity_positions = build_equity_positions(
            ranked_stocks=ranked,
            equity_exposure=equity_exposure,
            weighting_method=weighting_method,
            max_position=max_position,
        )
        defensive_positions = build_defensive_positions(defensive_exposure)
        positions = equity_positions + defensive_positions
        portfolio = TargetPortfolio(
            as_of_date=as_of_date,
            positions=positions,
            universe_name=best_universe,
            equity_exposure=equity_exposure,
            defensive_exposure=defensive_exposure,
        )
        portfolio.validate()
        _save_snapshot(session_obj, portfolio)
        return portfolio

    if session is not None:
        return _build(session)

    with get_session() as session_obj:
        return _build(session_obj)


def compute_rebalance(
    as_of_date: datetime.date, threshold: float = 0.005, session: Session | None = None
) -> List[RebalanceInstruction]:
    def _compute(session_obj: Session) -> List[RebalanceInstruction]:
        current = _load_last_snapshot(session_obj)
        if current is None:
            raise ValueError("No prior portfolio snapshot available")
        target = build_monthly_portfolio(as_of_date, session=session_obj)
        return diff_portfolios(current=current, target=target, threshold=threshold)

    if session is not None:
        return _compute(session)

    with get_session() as session_obj:
        return _compute(session_obj)


__all__ = ["build_monthly_portfolio", "compute_rebalance"]
