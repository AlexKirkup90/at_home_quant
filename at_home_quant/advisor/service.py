from __future__ import annotations

import datetime
import json
from dataclasses import asdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.advisor.models import (
    AdvisorRecommendationItem,
    AdvisorWatchItem,
    ExecutedPortfolioFromDecisions,
    WeeklyAdvisorReport,
    WorkflowDecisionInput,
)
from at_home_quant.data.tickers import TickerInfo, TickerType
from at_home_quant.db import crud
from at_home_quant.db.models import (
    AdvisorPortfolioSnapshot,
    Base,
    RecommendationDecision,
    WeeklyRecommendationBatch,
    WeeklyRecommendationItem,
)
from at_home_quant.db.session import get_session
from at_home_quant.portfolio.models import TargetPortfolio, TargetPosition
from at_home_quant.portfolio.rebalance import diff_portfolios
from at_home_quant.portfolio.service import build_monthly_portfolio
from at_home_quant.regime.service import get_current_regime
from at_home_quant.selection.service import rank_universe


def _serialize_positions(positions: list[TargetPosition]) -> str:
    return json.dumps([asdict(position) for position in positions])


def _deserialize_positions(positions_json: str) -> list[TargetPosition]:
    return [TargetPosition(**item) for item in json.loads(positions_json)]


def _snapshot_to_portfolio(snapshot: AdvisorPortfolioSnapshot) -> TargetPortfolio:
    return TargetPortfolio(
        as_of_date=snapshot.as_of_date,
        positions=_deserialize_positions(snapshot.positions_json),
        universe_name=snapshot.universe_name,
        equity_exposure=snapshot.equity_exposure,
        defensive_exposure=snapshot.defensive_exposure,
    )


def _ensure_symbols_exist(session: Session, positions: list[TargetPosition]) -> None:
    tickers = {
        position.ticker: TickerInfo(
            symbol=position.ticker,
            name=position.ticker,
            asset_type=TickerType.EQUITY,
            universe=None,
            currency="USD",
        )
        for position in positions
    }
    crud.upsert_tickers(session, tickers)


def save_advisor_portfolio_snapshot(
    as_of_date: datetime.date,
    positions: list[TargetPosition],
    snapshot_type: str,
    source: str = "app",
    universe_name: str = "USER_BASELINE",
    session: Session | None = None,
) -> TargetPortfolio:
    if snapshot_type not in {"baseline", "executed", "model_target"}:
        raise ValueError(f"Unsupported snapshot_type: {snapshot_type}")
    equity_exposure = sum(position.weight for position in positions if position.asset_type == "equity")
    defensive_exposure = max(0.0, 1.0 - equity_exposure)
    portfolio = TargetPortfolio(
        as_of_date=as_of_date,
        positions=positions,
        universe_name=universe_name,
        equity_exposure=equity_exposure,
        defensive_exposure=defensive_exposure,
    )
    portfolio.validate()

    def _save(session_obj: Session) -> TargetPortfolio:
        Base.metadata.create_all(bind=session_obj.bind)
        _ensure_symbols_exist(session_obj, positions)
        snapshot = AdvisorPortfolioSnapshot(
            as_of_date=as_of_date,
            snapshot_type=snapshot_type,
            source=source,
            universe_name=universe_name,
            equity_exposure=equity_exposure,
            defensive_exposure=defensive_exposure,
            positions_json=_serialize_positions(positions),
        )
        session_obj.add(snapshot)
        return portfolio

    if session is not None:
        return _save(session)

    with get_session() as session_obj:
        return _save(session_obj)


def get_latest_advisor_portfolio(
    snapshot_type: str,
    as_of_date: datetime.date | None = None,
    session: Session | None = None,
) -> TargetPortfolio | None:
    def _get(session_obj: Session) -> TargetPortfolio | None:
        Base.metadata.create_all(bind=session_obj.bind)
        stmt = select(AdvisorPortfolioSnapshot).where(AdvisorPortfolioSnapshot.snapshot_type == snapshot_type)
        if as_of_date is not None:
            stmt = stmt.where(AdvisorPortfolioSnapshot.as_of_date <= as_of_date)
        row = session_obj.execute(
            stmt.order_by(AdvisorPortfolioSnapshot.as_of_date.desc(), AdvisorPortfolioSnapshot.created_at.desc())
        ).scalars().first()
        if row is None:
            return None
        return _snapshot_to_portfolio(row)

    if session is not None:
        return _get(session)

    with get_session() as session_obj:
        return _get(session_obj)


def _action_rationale(action: str, delta: float, threshold: float) -> str:
    if action == "buy":
        return f"Increase by {delta:.2%} toward model target; exceeds {threshold:.2%} rebalance threshold."
    if action == "sell":
        return f"Reduce by {abs(delta):.2%} to align with model target and risk overlay limits."
    return f"Within threshold ({threshold:.2%}); keep current allocation."


def _build_watchlist(
    as_of_date: datetime.date,
    best_universe: str,
    top_n: int,
    target_portfolio: TargetPortfolio,
    session: Session,
) -> list[AdvisorWatchItem]:
    target_equities = {p.ticker for p in target_portfolio.positions if p.asset_type == "equity"}
    ranked = rank_universe(best_universe, as_of_date, top_n=top_n + 8, session=session)
    watchlist: list[AdvisorWatchItem] = []
    for item in ranked:
        if item.ticker in target_equities:
            continue
        watchlist.append(
            AdvisorWatchItem(
                ticker=item.ticker,
                composite_score=item.composite_score,
                reason="Ranked just below current buy cutoff; monitor for possible upgrade next cycle.",
            )
        )
        if len(watchlist) >= 5:
            break
    return watchlist


def generate_weekly_recommendation(
    as_of_date: datetime.date,
    top_n: int = 15,
    threshold: float = 0.005,
    session: Session | None = None,
) -> WeeklyAdvisorReport:
    def _generate(session_obj: Session) -> WeeklyAdvisorReport:
        Base.metadata.create_all(bind=session_obj.bind)
        current_portfolio = get_latest_advisor_portfolio(
            snapshot_type="executed",
            as_of_date=as_of_date,
            session=session_obj,
        )
        if current_portfolio is None:
            current_portfolio = get_latest_advisor_portfolio(
                snapshot_type="baseline",
                as_of_date=as_of_date,
                session=session_obj,
            )
        if current_portfolio is None:
            raise ValueError("No baseline/executed portfolio found. Complete Portfolio Onboarding first.")

        target_portfolio = build_monthly_portfolio(
            as_of_date=as_of_date,
            top_n=top_n,
            persist_snapshot=False,
            previous_portfolio=current_portfolio,
            session=session_obj,
        )
        save_advisor_portfolio_snapshot(
            as_of_date=as_of_date,
            positions=target_portfolio.positions,
            snapshot_type="model_target",
            source="weekly_engine",
            universe_name=target_portfolio.universe_name,
            session=session_obj,
        )

        regime = get_current_regime(as_of_date, session=session_obj)
        instructions = diff_portfolios(current=current_portfolio, target=target_portfolio, threshold=threshold)
        recommendations: list[AdvisorRecommendationItem] = []
        batch = WeeklyRecommendationBatch(
            as_of_date=as_of_date,
            best_universe=regime.best_universe,
            best_universe_score=regime.best_universe_score,
            status="open",
            watchlist_json="[]",
        )
        session_obj.add(batch)
        session_obj.flush()

        for instruction in instructions:
            rationale = _action_rationale(instruction.action, instruction.delta, threshold)
            row = WeeklyRecommendationItem(
                batch_id=batch.id,
                ticker=instruction.ticker,
                recommendation=instruction.action,
                current_weight=instruction.current_weight,
                target_weight=instruction.target_weight,
                delta=instruction.delta,
                rationale=rationale,
            )
            session_obj.add(row)
            session_obj.flush()
            recommendations.append(
                AdvisorRecommendationItem(
                    id=row.id,
                    ticker=row.ticker,
                    recommendation=row.recommendation,
                    current_weight=row.current_weight,
                    target_weight=row.target_weight,
                    delta=row.delta,
                    rationale=row.rationale,
                )
            )

        watchlist = _build_watchlist(
            as_of_date=as_of_date,
            best_universe=regime.best_universe,
            top_n=top_n,
            target_portfolio=target_portfolio,
            session=session_obj,
        )
        batch.watchlist_json = json.dumps([asdict(item) for item in watchlist])
        session_obj.flush()

        return WeeklyAdvisorReport(
            batch_id=batch.id,
            created_at=batch.created_at,
            as_of_date=batch.as_of_date,
            best_universe=batch.best_universe,
            best_universe_score=batch.best_universe_score,
            current_portfolio=current_portfolio,
            target_portfolio=target_portfolio,
            recommendations=recommendations,
            watchlist=watchlist,
        )

    if session is not None:
        return _generate(session)

    with get_session() as session_obj:
        return _generate(session_obj)


def _load_batch_report(batch: WeeklyRecommendationBatch, session: Session) -> WeeklyAdvisorReport:
    target_portfolio = get_latest_advisor_portfolio(
        snapshot_type="model_target",
        as_of_date=batch.as_of_date,
        session=session,
    )
    current_portfolio = get_latest_advisor_portfolio(
        snapshot_type="executed",
        as_of_date=batch.as_of_date,
        session=session,
    )
    if current_portfolio is None:
        current_portfolio = get_latest_advisor_portfolio(
            snapshot_type="baseline",
            as_of_date=batch.as_of_date,
            session=session,
        )
    if target_portfolio is None or current_portfolio is None:
        raise ValueError("Unable to reconstruct weekly report portfolios.")

    item_rows = session.execute(
        select(WeeklyRecommendationItem).where(WeeklyRecommendationItem.batch_id == batch.id).order_by(WeeklyRecommendationItem.ticker)
    ).scalars().all()
    decision_rows = session.execute(
        select(RecommendationDecision, WeeklyRecommendationItem.id)
        .join(WeeklyRecommendationItem, WeeklyRecommendationItem.id == RecommendationDecision.item_id)
        .where(WeeklyRecommendationItem.batch_id == batch.id)
    ).all()
    decision_by_item = {item_id: decision for decision, item_id in decision_rows}

    recommendations: list[AdvisorRecommendationItem] = []
    for row in item_rows:
        decision = decision_by_item.get(row.id)
        recommendations.append(
            AdvisorRecommendationItem(
                id=row.id,
                ticker=row.ticker,
                recommendation=row.recommendation,
                current_weight=row.current_weight,
                target_weight=row.target_weight,
                delta=row.delta,
                rationale=row.rationale,
                decision=decision.decision if decision is not None else None,
                executed_weight=decision.executed_weight if decision is not None else None,
                note=decision.note if decision is not None else None,
            )
        )

    watchlist_data = json.loads(batch.watchlist_json or "[]")
    watchlist = [AdvisorWatchItem(**item) for item in watchlist_data]

    return WeeklyAdvisorReport(
        batch_id=batch.id,
        created_at=batch.created_at,
        as_of_date=batch.as_of_date,
        best_universe=batch.best_universe,
        best_universe_score=batch.best_universe_score,
        current_portfolio=current_portfolio,
        target_portfolio=target_portfolio,
        recommendations=recommendations,
        watchlist=watchlist,
    )


def get_latest_weekly_report(
    as_of_date: datetime.date | None = None,
    session: Session | None = None,
) -> WeeklyAdvisorReport | None:
    def _get(session_obj: Session) -> WeeklyAdvisorReport | None:
        Base.metadata.create_all(bind=session_obj.bind)
        stmt = select(WeeklyRecommendationBatch)
        if as_of_date is not None:
            stmt = stmt.where(WeeklyRecommendationBatch.as_of_date <= as_of_date)
        batch = session_obj.execute(
            stmt.order_by(
                WeeklyRecommendationBatch.as_of_date.desc(),
                WeeklyRecommendationBatch.created_at.desc(),
            )
        ).scalars().first()
        if batch is None:
            return None
        return _load_batch_report(batch, session_obj)

    if session is not None:
        return _get(session)

    with get_session() as session_obj:
        return _get(session_obj)


def log_decision(
    decision_input: WorkflowDecisionInput,
    session: Session | None = None,
) -> None:
    if decision_input.decision not in {"follow", "ignore", "partial"}:
        raise ValueError(f"Unsupported decision: {decision_input.decision}")

    def _log(session_obj: Session) -> None:
        Base.metadata.create_all(bind=session_obj.bind)
        existing = session_obj.execute(
            select(RecommendationDecision).where(RecommendationDecision.item_id == decision_input.item_id)
        ).scalars().first()
        now = datetime.datetime.utcnow()
        if existing is None:
            row = RecommendationDecision(
                item_id=decision_input.item_id,
                decision=decision_input.decision,
                executed_weight=decision_input.executed_weight,
                note=decision_input.note,
                created_at=now,
                updated_at=now,
            )
            session_obj.add(row)
            return
        existing.decision = decision_input.decision
        existing.executed_weight = decision_input.executed_weight
        existing.note = decision_input.note
        existing.updated_at = now

    if session is not None:
        _log(session)
        return

    with get_session() as session_obj:
        _log(session_obj)


def _normalize_positions(positions: list[TargetPosition]) -> list[TargetPosition]:
    total = sum(position.weight for position in positions if position.weight > 0)
    if total <= 0:
        raise ValueError("No positive positions available to save executed portfolio.")
    return [
        TargetPosition(
            ticker=position.ticker,
            weight=position.weight / total,
            asset_type=position.asset_type,
        )
        for position in positions
        if position.weight > 0
    ]


def _asset_type_for_ticker(current: TargetPortfolio, target: TargetPortfolio, ticker: str) -> str:
    for position in target.positions + current.positions:
        if position.ticker == ticker:
            return position.asset_type
    return "equity"


def save_executed_from_decisions(
    batch_id: int,
    session: Session | None = None,
) -> ExecutedPortfolioFromDecisions:
    def _save(session_obj: Session) -> ExecutedPortfolioFromDecisions:
        Base.metadata.create_all(bind=session_obj.bind)
        batch = session_obj.execute(
            select(WeeklyRecommendationBatch).where(WeeklyRecommendationBatch.id == batch_id)
        ).scalars().first()
        if batch is None:
            raise ValueError(f"Recommendation batch {batch_id} was not found.")
        report = _load_batch_report(batch, session_obj)
        if not report.recommendations:
            raise ValueError("Recommendation batch is empty.")

        weights: dict[str, float] = {}
        followed = 0
        ignored = 0
        partial = 0
        for item in report.recommendations:
            decision = item.decision or "ignore"
            if decision == "follow":
                followed += 1
                weight = item.target_weight
            elif decision == "partial":
                partial += 1
                fallback = (item.current_weight + item.target_weight) / 2.0
                weight = fallback if item.executed_weight is None else item.executed_weight
            else:
                ignored += 1
                weight = item.current_weight
            weights[item.ticker] = max(0.0, weight)

        positions = _normalize_positions(
            [
                TargetPosition(
                    ticker=ticker,
                    weight=weight,
                    asset_type=_asset_type_for_ticker(report.current_portfolio, report.target_portfolio, ticker),
                )
                for ticker, weight in sorted(weights.items())
                if weight > 0
            ]
        )
        save_advisor_portfolio_snapshot(
            as_of_date=batch.as_of_date,
            positions=positions,
            snapshot_type="executed",
            source="decision_log",
            universe_name=report.target_portfolio.universe_name,
            session=session_obj,
        )
        batch.status = "closed"
        return ExecutedPortfolioFromDecisions(
            as_of_date=batch.as_of_date,
            positions=positions,
            followed_items=followed,
            ignored_items=ignored,
            partial_items=partial,
        )

    if session is not None:
        return _save(session)

    with get_session() as session_obj:
        return _save(session_obj)


__all__ = [
    "save_advisor_portfolio_snapshot",
    "get_latest_advisor_portfolio",
    "generate_weekly_recommendation",
    "get_latest_weekly_report",
    "log_decision",
    "save_executed_from_decisions",
]
