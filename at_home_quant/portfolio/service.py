from __future__ import annotations

import datetime
import json
from typing import List

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.data.health import assert_data_health_for_portfolio
from at_home_quant.data.tickers import sector_for_symbol
from at_home_quant.db.models import Base, PortfolioSnapshot, PriceDaily, Ticker
from at_home_quant.db.session import get_session
from at_home_quant.portfolio.models import (
    PortfolioRiskReport,
    RebalanceInstruction,
    RiskConstraintViolation,
    TargetPortfolio,
    TargetPosition,
)
from at_home_quant.portfolio.optimizer import (
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


def _load_last_snapshot(session: Session, before_date: datetime.date | None = None) -> TargetPortfolio | None:
    stmt = select(PortfolioSnapshot)
    if before_date is not None:
        stmt = stmt.where(PortfolioSnapshot.as_of_date < before_date)
    row = session.execute(
        stmt.order_by(PortfolioSnapshot.as_of_date.desc())
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


def _average_dollar_volume(
    session: Session,
    symbol: str,
    as_of_date: datetime.date,
    lookback_days: int,
) -> float | None:
    rows = session.execute(
        select(PriceDaily.adj_close, PriceDaily.volume)
        .join(Ticker, Ticker.id == PriceDaily.ticker_id)
        .where(
            Ticker.symbol == symbol,
            PriceDaily.date <= as_of_date,
            PriceDaily.volume.is_not(None),
        )
        .order_by(PriceDaily.date.desc())
        .limit(lookback_days)
    ).all()
    values = [float(price) * float(volume) for price, volume in rows if price is not None and volume is not None]
    if not values:
        return None
    return float(sum(values) / len(values))


def _compute_turnover(previous_portfolio: TargetPortfolio, next_portfolio: TargetPortfolio) -> float:
    previous = {position.ticker: position.weight for position in previous_portfolio.positions}
    nxt = {position.ticker: position.weight for position in next_portfolio.positions}
    tickers = set(previous) | set(nxt)
    gross_weight_change = sum(abs(nxt.get(ticker, 0.0) - previous.get(ticker, 0.0)) for ticker in tickers)
    return 0.5 * gross_weight_change


def _filter_by_liquidity(
    ranked,
    session: Session,
    as_of_date: datetime.date,
    min_adv_usd: float,
    lookback_days: int,
):
    kept = []
    adv_by_ticker: dict[str, float | None] = {}
    for row in ranked:
        adv = _average_dollar_volume(session, row.ticker, as_of_date, lookback_days)
        adv_by_ticker[row.ticker] = adv
        if adv is None or adv >= min_adv_usd:
            kept.append(row)
    return kept, adv_by_ticker


def _apply_sector_cap(
    equity_positions: list[TargetPosition],
    max_sector_weight: float,
) -> tuple[list[TargetPosition], float]:
    if not equity_positions:
        return [], 0.0
    by_sector: dict[str, list[TargetPosition]] = {}
    for position in equity_positions:
        sector = sector_for_symbol(position.ticker)
        by_sector.setdefault(sector, []).append(position)

    adjusted: list[TargetPosition] = []
    for sector_positions in by_sector.values():
        sector_weight = sum(item.weight for item in sector_positions)
        if sector_weight <= max_sector_weight:
            adjusted.extend(sector_positions)
            continue
        scale = max_sector_weight / sector_weight if sector_weight > 0 else 0.0
        adjusted.extend(
            TargetPosition(ticker=item.ticker, weight=item.weight * scale, asset_type=item.asset_type)
            for item in sector_positions
        )

    adjusted_equity = sum(item.weight for item in adjusted)
    original_equity = sum(item.weight for item in equity_positions)
    leftover = max(0.0, original_equity - adjusted_equity)
    return adjusted, leftover


def _apply_absolute_max_position(
    equity_positions: list[TargetPosition],
    max_position: float,
) -> tuple[list[TargetPosition], float]:
    if not equity_positions:
        return [], 0.0
    adjusted = [
        TargetPosition(
            ticker=position.ticker,
            weight=min(position.weight, max_position),
            asset_type=position.asset_type,
        )
        for position in equity_positions
    ]
    adjusted_equity = sum(position.weight for position in adjusted)
    original_equity = sum(position.weight for position in equity_positions)
    leftover = max(0.0, original_equity - adjusted_equity)
    return adjusted, leftover


def _normalize_positions(positions: list[TargetPosition]) -> list[TargetPosition]:
    total = sum(max(0.0, item.weight) for item in positions)
    if total <= 0:
        return []
    normalized = [
        TargetPosition(
            ticker=item.ticker,
            weight=max(0.0, item.weight) / total,
            asset_type=item.asset_type,
        )
        for item in positions
        if item.weight > 0
    ]
    return normalized


def _apply_turnover_cap(
    previous: TargetPortfolio,
    target: TargetPortfolio,
    max_turnover: float,
) -> tuple[TargetPortfolio, float]:
    turnover = _compute_turnover(previous, target)
    if turnover <= max_turnover or turnover <= 0:
        return target, turnover

    scale = max_turnover / turnover
    prev_weights = {item.ticker: item.weight for item in previous.positions}
    prev_types = {item.ticker: item.asset_type for item in previous.positions}
    target_weights = {item.ticker: item.weight for item in target.positions}
    target_types = {item.ticker: item.asset_type for item in target.positions}
    all_tickers = sorted(set(prev_weights) | set(target_weights))

    blended_positions: list[TargetPosition] = []
    for ticker in all_tickers:
        prev_weight = prev_weights.get(ticker, 0.0)
        tgt_weight = target_weights.get(ticker, 0.0)
        blended_weight = prev_weight + (tgt_weight - prev_weight) * scale
        if blended_weight <= 0:
            continue
        asset_type = target_types.get(ticker, prev_types.get(ticker, "equity"))
        blended_positions.append(
            TargetPosition(
                ticker=ticker,
                weight=blended_weight,
                asset_type=asset_type,
            )
        )
    normalized_positions = _normalize_positions(blended_positions)
    equity_exposure = sum(item.weight for item in normalized_positions if item.asset_type == "equity")
    portfolio = TargetPortfolio(
        as_of_date=target.as_of_date,
        positions=normalized_positions,
        universe_name=target.universe_name,
        equity_exposure=equity_exposure,
        defensive_exposure=max(0.0, 1.0 - equity_exposure),
    )
    return portfolio, _compute_turnover(previous, portfolio)


def _build_risk_report(
    portfolio: TargetPortfolio,
    previous_portfolio: TargetPortfolio | None,
    adv_by_ticker: dict[str, float | None],
    max_position: float,
    max_sector_weight: float,
    max_turnover: float,
    min_adv_usd: float,
) -> PortfolioRiskReport:
    violations: list[RiskConstraintViolation] = []
    equity_weights = [item.weight for item in portfolio.positions if item.asset_type == "equity"]
    max_position_weight = max(equity_weights, default=0.0)
    if max_position_weight > max_position + 1e-9:
        violations.append(
            RiskConstraintViolation(
                code="max_position_breach",
                message=(
                    f"Max position {max_position_weight:.2%} exceeds limit {max_position:.2%}."
                ),
                current_value=max_position_weight,
                limit_value=max_position,
            )
        )

    sector_weights: dict[str, float] = {}
    for position in portfolio.positions:
        if position.asset_type != "equity":
            continue
        sector = sector_for_symbol(position.ticker)
        sector_weights[sector] = sector_weights.get(sector, 0.0) + position.weight
    max_sector = max(sector_weights.values()) if sector_weights else 0.0
    if max_sector > max_sector_weight + 1e-9:
        violations.append(
            RiskConstraintViolation(
                code="sector_cap_breach",
                message=(
                    f"Max sector weight {max_sector:.2%} exceeds limit {max_sector_weight:.2%}."
                ),
                current_value=max_sector,
                limit_value=max_sector_weight,
            )
        )

    turnover = 0.0
    if previous_portfolio is not None:
        turnover = _compute_turnover(previous_portfolio, portfolio)
        if turnover > max_turnover + 1e-9:
            violations.append(
                RiskConstraintViolation(
                    code="turnover_breach",
                    message=f"Turnover {turnover:.2%} exceeds limit {max_turnover:.2%}.",
                    current_value=turnover,
                    limit_value=max_turnover,
                )
            )

    adv_values = [adv_by_ticker.get(item.ticker) for item in portfolio.positions if item.asset_type == "equity"]
    known_adv_values = [value for value in adv_values if value is not None]
    min_adv_in_portfolio = min(known_adv_values) if known_adv_values else None
    for position in portfolio.positions:
        if position.asset_type != "equity":
            continue
        adv = adv_by_ticker.get(position.ticker)
        if adv is None:
            continue
        if adv < min_adv_usd:
            violations.append(
                RiskConstraintViolation(
                    code="liquidity_breach",
                    message=(
                        f"{position.ticker} ADV ${adv:,.0f} is below minimum ${min_adv_usd:,.0f}."
                    ),
                    current_value=adv,
                    limit_value=min_adv_usd,
                )
            )

    return PortfolioRiskReport(
        as_of_date=portfolio.as_of_date,
        max_position_weight=max_position_weight,
        max_sector_weight=max_sector,
        turnover=turnover,
        min_adv_usd_in_portfolio=min_adv_in_portfolio,
        violations=violations,
    )


def _risk_violation_summary(report: PortfolioRiskReport) -> str:
    return "; ".join(item.message for item in report.violations)


def save_manual_portfolio_snapshot(
    as_of_date: datetime.date,
    positions: list[TargetPosition],
    universe_name: str = "USER_BASELINE",
    session: Session | None = None,
) -> TargetPortfolio:
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
        _save_snapshot(session_obj, portfolio)
        return portfolio

    if session is not None:
        return _save(session)

    with get_session() as session_obj:
        return _save(session_obj)


def build_monthly_portfolio(
    as_of_date: datetime.date,
    top_n: int = 15,
    max_position: float | None = None,
    weighting_method: str = "softmax",
    persist_snapshot: bool = True,
    previous_portfolio: TargetPortfolio | None = None,
    session: Session | None = None,
) -> TargetPortfolio:
    def _build(session_obj: Session) -> TargetPortfolio:
        settings = get_settings()
        resolved_max_position = settings.risk_max_position if max_position is None else max_position
        resolved_max_sector_weight = settings.risk_max_sector_weight
        resolved_max_turnover = settings.risk_max_turnover
        resolved_min_adv = settings.risk_min_adv_usd
        resolved_adv_lookback = settings.risk_adv_lookback_days
        assert_data_health_for_portfolio(as_of_date, session=session_obj)
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
        ranked, adv_by_ticker = _filter_by_liquidity(
            ranked=ranked,
            session=session_obj,
            as_of_date=as_of_date,
            min_adv_usd=resolved_min_adv,
            lookback_days=resolved_adv_lookback,
        )
        if not ranked:
            equity_exposure = 0.0
            defensive_exposure = 1.0

        equity_positions = build_equity_positions(
            ranked_stocks=ranked,
            equity_exposure=equity_exposure,
            weighting_method=weighting_method,
            max_position=resolved_max_position,
        )
        capped_by_position, _ = _apply_absolute_max_position(
            equity_positions=equity_positions,
            max_position=resolved_max_position,
        )
        capped_equity_positions, _ = _apply_sector_cap(
            equity_positions=capped_by_position,
            max_sector_weight=resolved_max_sector_weight,
        )
        adjusted_equity_exposure = sum(position.weight for position in capped_equity_positions)
        adjusted_defensive_exposure = max(0.0, 1.0 - adjusted_equity_exposure)
        defensive_positions = build_defensive_positions(adjusted_defensive_exposure)
        positions = capped_equity_positions + defensive_positions
        portfolio = TargetPortfolio(
            as_of_date=as_of_date,
            positions=positions,
            universe_name=best_universe,
            equity_exposure=adjusted_equity_exposure,
            defensive_exposure=adjusted_defensive_exposure,
        )
        previous = previous_portfolio or _load_last_snapshot(session_obj, before_date=as_of_date)
        if previous is not None:
            portfolio, _ = _apply_turnover_cap(previous, portfolio, resolved_max_turnover)
            portfolio.validate()
        risk_report = _build_risk_report(
            portfolio=portfolio,
            previous_portfolio=previous,
            adv_by_ticker=adv_by_ticker,
            max_position=resolved_max_position,
            max_sector_weight=resolved_max_sector_weight,
            max_turnover=resolved_max_turnover,
            min_adv_usd=resolved_min_adv,
        )
        portfolio.risk_report = risk_report
        portfolio.validate()
        if persist_snapshot and settings.data_mode == "production" and not risk_report.is_within_limits:
            raise ValueError(f"Risk overlay gate failed: {_risk_violation_summary(risk_report)}")
        if persist_snapshot:
            _save_snapshot(session_obj, portfolio)
        return portfolio

    if session is not None:
        return _build(session)

    with get_session() as session_obj:
        return _build(session_obj)


def compute_rebalance(
    as_of_date: datetime.date,
    threshold: float = 0.005,
    top_n: int = 15,
    session: Session | None = None,
) -> List[RebalanceInstruction]:
    def _compute(session_obj: Session) -> List[RebalanceInstruction]:
        current = _load_last_snapshot(session_obj, before_date=as_of_date)
        if current is None:
            raise ValueError(f"No prior portfolio snapshot available before {as_of_date}")
        target = build_monthly_portfolio(
            as_of_date,
            top_n=top_n,
            persist_snapshot=False,
            session=session_obj,
        )
        return diff_portfolios(current=current, target=target, threshold=threshold)

    if session is not None:
        return _compute(session)

    with get_session() as session_obj:
        return _compute(session_obj)


__all__ = ["build_monthly_portfolio", "compute_rebalance", "save_manual_portfolio_snapshot"]
