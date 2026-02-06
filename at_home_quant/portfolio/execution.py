from __future__ import annotations

import datetime
from typing import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.db.models import PriceDaily, Ticker
from at_home_quant.portfolio.models import RebalanceInstruction


def _average_dollar_volume(
    session: Session,
    symbol: str,
    as_of_date: datetime.date,
    lookback_days: int = 20,
) -> float | None:
    rows = session.execute(
        select(PriceDaily.adj_close, PriceDaily.volume)
        .join(Ticker, Ticker.id == PriceDaily.ticker_id)
        .where(
            Ticker.symbol == symbol,
            PriceDaily.date <= as_of_date,
            PriceDaily.volume.is_not(None),
            PriceDaily.adj_close.is_not(None),
        )
        .order_by(PriceDaily.date.desc())
        .limit(lookback_days)
    ).all()
    values = [float(price) * float(volume) for price, volume in rows if price is not None and volume is not None]
    if not values:
        return None
    return float(sum(values) / len(values))


def estimate_execution_cost(
    session: Session,
    symbol: str,
    abs_delta_weight: float,
    as_of_date: datetime.date,
) -> dict:
    settings = get_settings()
    portfolio_value_usd = max(0.0, float(settings.execution_portfolio_value_usd))
    trade_notional_usd = abs_delta_weight * portfolio_value_usd
    adv_usd = _average_dollar_volume(
        session=session,
        symbol=symbol,
        as_of_date=as_of_date,
        lookback_days=settings.risk_adv_lookback_days,
    )
    adv_participation = None
    if adv_usd is not None and adv_usd > 0:
        adv_participation = trade_notional_usd / adv_usd

    base_cost_bps = float(settings.transaction_cost_bps) + float(settings.slippage_bps)
    impact_bps = 0.0
    if adv_participation is not None:
        impact_ref = max(1e-9, float(settings.execution_impact_bps_at_10pct_adv))
        normalized_participation = adv_participation / 0.10
        impact_bps = impact_ref * (normalized_participation**2)
    total_cost_bps = base_cost_bps + impact_bps
    cost_usd = trade_notional_usd * (total_cost_bps / 10_000.0)
    cost_pct_of_portfolio = abs_delta_weight * (total_cost_bps / 10_000.0)
    return {
        "trade_notional_usd": trade_notional_usd,
        "adv_usd": adv_usd,
        "adv_participation": adv_participation,
        "base_cost_bps": base_cost_bps,
        "impact_bps": impact_bps,
        "total_cost_bps": total_cost_bps,
        "cost_usd": cost_usd,
        "cost_pct_of_portfolio": cost_pct_of_portfolio,
    }


def evaluate_pretrade_checks(
    session: Session,
    instructions: Iterable[RebalanceInstruction],
    as_of_date: datetime.date,
) -> dict:
    settings = get_settings()
    min_ticket_usd = max(0.0, float(settings.execution_min_ticket_usd))
    max_adv_participation = max(0.0, float(settings.execution_max_adv_participation))
    checks: list[dict] = []
    blocked_count = 0
    estimated_shortfall_pct = 0.0
    estimated_shortfall_usd = 0.0
    max_seen_participation = 0.0

    for instruction in instructions:
        abs_delta = abs(float(instruction.delta))
        effective_abs_delta = abs_delta if instruction.action in {"buy", "sell"} else 0.0
        estimate = estimate_execution_cost(
            session=session,
            symbol=instruction.ticker,
            abs_delta_weight=effective_abs_delta,
            as_of_date=as_of_date,
        )
        blocked_reason = None
        if instruction.action in {"buy", "sell"} and abs_delta > 0:
            if estimate["trade_notional_usd"] < min_ticket_usd:
                blocked_reason = (
                    f"ticket ${estimate['trade_notional_usd']:,.0f} below minimum "
                    f"${min_ticket_usd:,.0f}"
                )
            adv_participation = estimate["adv_participation"]
            if (
                blocked_reason is None
                and adv_participation is not None
                and adv_participation > max_adv_participation
            ):
                blocked_reason = (
                    f"ADV participation {adv_participation:.2%} exceeds "
                    f"limit {max_adv_participation:.2%}"
                )
        if blocked_reason is not None:
            blocked_count += 1
        if estimate["adv_participation"] is not None:
            max_seen_participation = max(max_seen_participation, float(estimate["adv_participation"]))
        estimated_shortfall_pct += float(estimate["cost_pct_of_portfolio"])
        estimated_shortfall_usd += float(estimate["cost_usd"])
        checks.append(
            {
                "ticker": instruction.ticker,
                "action": instruction.action,
                "delta": float(instruction.delta),
                "trade_notional_usd": float(estimate["trade_notional_usd"]),
                "adv_usd": estimate["adv_usd"],
                "adv_participation": estimate["adv_participation"],
                "total_cost_bps": float(estimate["total_cost_bps"]),
                "cost_usd": float(estimate["cost_usd"]),
                "blocked": blocked_reason is not None,
                "blocked_reason": blocked_reason,
            }
        )

    return {
        "is_passing": blocked_count == 0,
        "blocked_count": blocked_count,
        "portfolio_value_usd": float(settings.execution_portfolio_value_usd),
        "min_ticket_usd": min_ticket_usd,
        "max_adv_participation_limit": max_adv_participation,
        "max_adv_participation_seen": max_seen_participation,
        "estimated_shortfall_pct": estimated_shortfall_pct,
        "estimated_shortfall_usd": estimated_shortfall_usd,
        "checks": checks,
    }


def apply_pretrade_policy(
    instructions: Iterable[RebalanceInstruction],
    pretrade_report: dict,
) -> list[RebalanceInstruction]:
    blocked_reasons = {
        item["ticker"]: item["blocked_reason"]
        for item in pretrade_report.get("checks", [])
        if item.get("blocked")
    }
    updated: list[RebalanceInstruction] = []
    for instruction in instructions:
        reason = blocked_reasons.get(instruction.ticker)
        if reason is None or instruction.action == "hold":
            updated.append(instruction)
            continue
        updated.append(
            RebalanceInstruction(
                ticker=instruction.ticker,
                action="hold",
                current_weight=instruction.current_weight,
                target_weight=instruction.current_weight,
                delta=0.0,
                policy_status="blocked",
                policy_reason=f"Pre-trade block: {reason}",
            )
        )
    return updated


__all__ = [
    "estimate_execution_cost",
    "evaluate_pretrade_checks",
    "apply_pretrade_policy",
]
