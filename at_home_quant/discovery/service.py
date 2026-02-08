from __future__ import annotations

import datetime
import json
import math
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.data.tickers import BENCHMARKS, Universe
from at_home_quant.db.models import (
    AdvisorPortfolioSnapshot,
    Base,
    DiscoveryCandidate,
    DiscoveryRun,
    PriceDaily,
    Ticker,
)
from at_home_quant.db.session import get_session
from at_home_quant.discovery.models import DiscoveryCandidateItem, DiscoveryRunReport
from at_home_quant.ops.audit import append_audit_event
from at_home_quant.regime.service import get_current_regime
from at_home_quant.selection.models import StockFactorScores
from at_home_quant.selection.service import rank_universe


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


MAJOR_RISK_FLAGS = {
    "High volatility",
    "Negative 12m momentum",
    "Negative 6m momentum",
}


def _tier_label(score: float, risk_flags: list[str]) -> str:
    major_count = sum(1 for flag in risk_flags if flag in MAJOR_RISK_FLAGS)
    if score >= 80.0 and major_count == 0:
        return "Strong Buy"
    if score >= 60.0 and major_count <= 1:
        return "Consider Buy"
    return "Watch Closely"


def _risk_flags(item: StockFactorScores) -> list[str]:
    flags: list[str] = []
    if item.volatility > 0.45:
        flags.append("High volatility")
    if item.momentum_12m < 0:
        flags.append("Negative 12m momentum")
    if item.momentum_6m < 0:
        flags.append("Negative 6m momentum")
    if item.stability < 0.55:
        flags.append("Low stability")
    return flags


def _factor_contributions(item: StockFactorScores) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    positive: list[tuple[str, float]] = []
    negative: list[tuple[str, float]] = []
    if item.momentum_12m >= 0:
        positive.append(("12m momentum", _clip(item.momentum_12m / 0.25, 0.0, 1.0) * 30.0))
    else:
        negative.append(("12m momentum", _clip(abs(item.momentum_12m) / 0.25, 0.0, 1.0) * 30.0))
    if item.momentum_6m >= 0:
        positive.append(("6m momentum", _clip(item.momentum_6m / 0.15, 0.0, 1.0) * 20.0))
    else:
        negative.append(("6m momentum", _clip(abs(item.momentum_6m) / 0.15, 0.0, 1.0) * 20.0))
    accel = item.momentum_6m - item.momentum_12m
    if accel >= 0:
        positive.append(("momentum acceleration", _clip(accel / 0.10, 0.0, 1.0) * 10.0))
    else:
        negative.append(("momentum deceleration", _clip(abs(accel) / 0.10, 0.0, 1.0) * 10.0))
    vol_bonus = 0.40 - item.volatility
    if vol_bonus >= 0:
        positive.append(("volatility control", _clip(vol_bonus / 0.30, 0.0, 1.0) * 15.0))
    else:
        negative.append(("elevated volatility", _clip(abs(vol_bonus) / 0.30, 0.0, 1.0) * 15.0))
    stability_center = item.stability - 0.55
    if stability_center >= 0:
        positive.append(("stability", _clip(stability_center / 0.35, 0.0, 1.0) * 15.0))
    else:
        negative.append(("low stability", _clip(abs(stability_center) / 0.35, 0.0, 1.0) * 15.0))
    positive.append(("value", _clip((item.value - 0.03) / 0.03, -1.0, 1.0) * 5.0))
    positive.append(("shareholder yield", _clip((item.shareholder_yield - 0.02) / 0.03, -1.0, 1.0) * 5.0))
    positive = [pair for pair in positive if pair[1] > 0]
    negative = [pair for pair in negative if pair[1] > 0]
    return sorted(positive, key=lambda pair: pair[1], reverse=True), sorted(
        negative, key=lambda pair: pair[1], reverse=True
    )


def _flag_to_signed_risk(flag: str) -> str:
    normalized = flag.strip().lower()
    if "12m momentum" in normalized:
        return "-12m momentum"
    if "6m momentum" in normalized:
        return "-6m momentum"
    if "volatility" in normalized:
        return "-high volatility"
    if "stability" in normalized:
        return "-low stability"
    return f"-{flag.lower()}"


def _raw_discovery_signal(item: StockFactorScores) -> float:
    contributions = {
        "momentum_12m": _clip(item.momentum_12m / 0.25, -1.0, 1.0) * 30.0,
        "momentum_6m": _clip(item.momentum_6m / 0.15, -1.0, 1.0) * 20.0,
        "acceleration": _clip((item.momentum_6m - item.momentum_12m) / 0.10, -1.0, 1.0) * 10.0,
        "volatility_quality": _clip((0.40 - item.volatility) / 0.30, -1.0, 1.0) * 15.0,
        "stability": _clip((item.stability - 0.55) / 0.35, -1.0, 1.0) * 15.0,
        "value": _clip((item.value - 0.03) / 0.03, -1.0, 1.0) * 5.0,
        "shareholder_yield": _clip((item.shareholder_yield - 0.02) / 0.03, -1.0, 1.0) * 5.0,
    }
    return float(sum(contributions.values()))


def _normalize_scores(raw_scores: list[float]) -> list[float]:
    if not raw_scores:
        return []
    mean = sum(raw_scores) / len(raw_scores)
    variance = sum((score - mean) ** 2 for score in raw_scores) / len(raw_scores)
    std = math.sqrt(variance)
    if std <= 1e-9:
        return [50.0 for _ in raw_scores]
    normalized = [50.0 + 15.0 * ((score - mean) / std) for score in raw_scores]
    return [_clip(score, 0.0, 100.0) for score in normalized]


def _soften_score_delta(
    raw_delta: float | None,
    *,
    eligible_pool_size: int,
    min_confidence_pool: int,
    cap: float,
) -> float | None:
    if raw_delta is None:
        return None
    pool_floor = max(1, min_confidence_pool)
    confidence_scale = min(1.0, eligible_pool_size / pool_floor)
    softened = raw_delta * confidence_scale
    cap_value = abs(cap)
    return _clip(softened, -cap_value, cap_value)


def _rationale(
    item: StockFactorScores,
    flags: list[str],
) -> str:
    drivers, detractors = _factor_contributions(item)
    top_drivers = [f"+{label}" for label, _weight in drivers[:2]]
    top_risks = [f"-{label}" for label, _weight in detractors[:1]]
    if not top_risks and flags:
        top_risks = [_flag_to_signed_risk(flag) for flag in flags[:1]]
    driver_text = "+mixed signals" if not top_drivers else ", ".join(top_drivers)
    risk_text = "-none flagged" if not top_risks else ", ".join(top_risks)
    return f"Top drivers: {driver_text}. Top risks: {risk_text}."


def _symbol_market_stats(
    session: Session,
    symbol: str,
    *,
    as_of_date: datetime.date,
    adv_lookback: int,
) -> tuple[int, float | None]:
    history_days = (
        session.execute(
            select(func.count(PriceDaily.id))
            .join(Ticker, Ticker.id == PriceDaily.ticker_id)
            .where(Ticker.symbol == symbol, PriceDaily.date <= as_of_date)
        ).scalar_one()
        or 0
    )
    rows = session.execute(
        select(PriceDaily.adj_close, PriceDaily.volume)
        .join(Ticker, Ticker.id == PriceDaily.ticker_id)
        .where(Ticker.symbol == symbol, PriceDaily.date <= as_of_date)
        .order_by(PriceDaily.date.desc())
        .limit(max(1, adv_lookback))
    ).all()
    dollars = [
        float(adj_close) * float(volume)
        for adj_close, volume in rows
        if adj_close is not None and volume is not None
    ]
    adv_usd = None if not dollars else (sum(dollars) / len(dollars))
    return int(history_days), adv_usd


def _deserialize_positions(positions_json: str) -> list[dict]:
    try:
        payload = json.loads(positions_json)
    except Exception:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _current_holdings(session: Session, as_of_date: datetime.date) -> set[str]:
    for snapshot_type in ("executed", "baseline"):
        row = session.execute(
            select(AdvisorPortfolioSnapshot)
            .where(
                AdvisorPortfolioSnapshot.snapshot_type == snapshot_type,
                AdvisorPortfolioSnapshot.as_of_date <= as_of_date,
            )
            .order_by(AdvisorPortfolioSnapshot.as_of_date.desc(), AdvisorPortfolioSnapshot.created_at.desc())
        ).scalars().first()
        if row is None:
            continue
        holdings = {
            str(item.get("ticker"))
            for item in _deserialize_positions(row.positions_json)
            if item.get("ticker")
        }
        if holdings:
            return holdings
    return set()


def _run_summary(candidates: Iterable[DiscoveryCandidateItem]) -> dict:
    tier_counts: dict[str, int] = {}
    for candidate in candidates:
        tier_counts[candidate.tier] = tier_counts.get(candidate.tier, 0) + 1
    return {"tier_counts": tier_counts}


def _serialize_risk_flags(flags: list[str]) -> str:
    return json.dumps(flags)


def _deserialize_risk_flags(payload: str) -> list[str]:
    try:
        decoded = json.loads(payload or "[]")
    except Exception:
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded]


def _parse_summary(payload: str | None) -> dict:
    try:
        decoded = json.loads(payload or "{}")
    except Exception:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _build_run_report(session: Session, run: DiscoveryRun, limit: int = 100) -> DiscoveryRunReport:
    candidates = session.execute(
        select(DiscoveryCandidate)
        .where(DiscoveryCandidate.run_id == run.id)
        .order_by(DiscoveryCandidate.discovery_score.desc(), DiscoveryCandidate.ticker.asc())
        .limit(limit)
    ).scalars().all()
    items = [
        DiscoveryCandidateItem(
            ticker=row.ticker,
            source_universe=row.source_universe,
            composite_score=float(row.composite_score),
            discovery_score=float(row.discovery_score),
            tier=row.tier,
            rationale=row.rationale,
            risk_flags=_deserialize_risk_flags(row.risk_flags_json),
            score_delta=(None if row.score_delta is None else float(row.score_delta)),
            is_current_holding=bool(row.is_current_holding),
        )
        for row in candidates
    ]
    return DiscoveryRunReport(
        run_id=run.id,
        created_at=run.created_at,
        as_of_date=run.as_of_date,
        status=run.status,
        best_universe=run.best_universe,
        data_snapshot_hash=run.data_snapshot_hash,
        experiment_id=run.experiment_id,
        candidate_count=run.candidate_count,
        summary=_parse_summary(run.summary_json),
        error_message=run.error_message,
        candidates=items,
    )


def run_discovery_scan(
    as_of_date: datetime.date,
    data_snapshot_hash: str | None = None,
    experiment_id: int | None = None,
    max_per_universe: int = 30,
    max_candidates: int = 60,
    session: Session | None = None,
) -> DiscoveryRunReport:
    def _run(session_obj: Session) -> DiscoveryRunReport:
        settings = get_settings()
        Base.metadata.create_all(bind=session_obj.bind)
        try:
            regime = get_current_regime(as_of_date, session=session_obj)
            best_universe = regime.best_universe
        except Exception:
            best_universe = None
        run = DiscoveryRun(
            as_of_date=as_of_date,
            status="running",
            best_universe=best_universe,
            data_snapshot_hash=data_snapshot_hash,
            experiment_id=experiment_id,
            candidate_count=0,
            summary_json="{}",
        )
        session_obj.add(run)
        session_obj.flush()
        append_audit_event(
            session_obj,
            event_type="discovery_run_started",
            entity_type="discovery_run",
            entity_id=str(run.id),
            payload={"as_of_date": as_of_date.isoformat(), "best_universe": best_universe},
        )

        try:
            holdings = _current_holdings(session_obj, as_of_date)
            previous_run = session_obj.execute(
                select(DiscoveryRun)
                .where(
                    DiscoveryRun.status == "succeeded",
                    DiscoveryRun.as_of_date <= as_of_date,
                    DiscoveryRun.id != run.id,
                )
                .order_by(DiscoveryRun.as_of_date.desc(), DiscoveryRun.created_at.desc())
            ).scalars().first()
            previous_scores: dict[str, float] = {}
            if previous_run is not None:
                previous_rows = session_obj.execute(
                    select(DiscoveryCandidate.ticker, DiscoveryCandidate.discovery_score).where(
                        DiscoveryCandidate.run_id == previous_run.id
                    )
                ).all()
                previous_scores = {ticker: float(score) for ticker, score in previous_rows}

            benchmark_exclusions = set(BENCHMARKS.keys()) | set(settings.benchmark_tickers)
            candidate_map: dict[str, dict] = {}
            excluded_counts = {
                "benchmark_etf": 0,
                "insufficient_history": 0,
                "insufficient_liquidity": 0,
            }
            universes = [u for u in Universe if u != Universe.BENCHMARK]
            for universe in universes:
                ranked = rank_universe(
                    universe.name,
                    as_of_date,
                    top_n=max_per_universe,
                    session=session_obj,
                )
                for item in ranked:
                    if settings.discovery_exclude_benchmark_etfs and item.ticker in benchmark_exclusions:
                        excluded_counts["benchmark_etf"] += 1
                        continue
                    history_days, adv_usd = _symbol_market_stats(
                        session_obj,
                        item.ticker,
                        as_of_date=as_of_date,
                        adv_lookback=settings.risk_adv_lookback_days,
                    )
                    if history_days < settings.discovery_min_history_days:
                        excluded_counts["insufficient_history"] += 1
                        continue
                    if adv_usd is None or adv_usd < settings.discovery_min_adv_usd:
                        excluded_counts["insufficient_liquidity"] += 1
                        continue
                    flags = _risk_flags(item)
                    raw_score = _raw_discovery_signal(item)
                    record = {
                        "ticker": item.ticker,
                        "source_universe": universe.name,
                        "composite_score": float(item.composite_score),
                        "raw_score": raw_score,
                        "rationale": _rationale(item, flags),
                        "risk_flags": flags,
                        "is_current_holding": (item.ticker in holdings),
                    }
                    existing = candidate_map.get(item.ticker)
                    if existing is None or record["raw_score"] > existing["raw_score"]:
                        candidate_map[item.ticker] = record

            provisional = list(candidate_map.values())
            normalized_scores = _normalize_scores([item["raw_score"] for item in provisional])
            merged_candidates: list[DiscoveryCandidateItem] = []
            eligible_pool_size = len(provisional)
            low_confidence = eligible_pool_size < settings.discovery_min_confidence_pool
            for item, normalized_score in zip(provisional, normalized_scores):
                previous = previous_scores.get(item["ticker"])
                raw_delta = None if previous is None else (normalized_score - previous)
                score_delta = _soften_score_delta(
                    raw_delta,
                    eligible_pool_size=eligible_pool_size,
                    min_confidence_pool=settings.discovery_min_confidence_pool,
                    cap=settings.discovery_score_delta_cap,
                )
                merged_candidates.append(
                    DiscoveryCandidateItem(
                        ticker=item["ticker"],
                        source_universe=item["source_universe"],
                        composite_score=item["composite_score"],
                        discovery_score=normalized_score,
                        tier=_tier_label(normalized_score, item["risk_flags"]),
                        rationale=item["rationale"],
                        risk_flags=item["risk_flags"],
                        score_delta=score_delta,
                        is_current_holding=item["is_current_holding"],
                    )
                )

            selected = sorted(
                merged_candidates,
                key=lambda item: (item.discovery_score, item.composite_score),
                reverse=True,
            )[:max_candidates]
            for item in selected:
                session_obj.add(
                    DiscoveryCandidate(
                        run_id=run.id,
                        as_of_date=as_of_date,
                        ticker=item.ticker,
                        source_universe=item.source_universe,
                        composite_score=item.composite_score,
                        discovery_score=item.discovery_score,
                        tier=item.tier,
                        rationale=item.rationale,
                        risk_flags_json=_serialize_risk_flags(item.risk_flags),
                        score_delta=item.score_delta,
                        is_current_holding=(1 if item.is_current_holding else 0),
                    )
                )

            run.status = "succeeded"
            run.candidate_count = len(selected)
            summary = _run_summary(selected)
            summary["excluded_counts"] = excluded_counts
            summary["eligible_pool_size"] = eligible_pool_size
            summary["low_confidence"] = low_confidence
            run.summary_json = json.dumps(summary)
            run.error_message = None
            append_audit_event(
                session_obj,
                event_type="discovery_run_succeeded",
                entity_type="discovery_run",
                entity_id=str(run.id),
                payload={
                    "as_of_date": as_of_date.isoformat(),
                    "candidate_count": len(selected),
                    "best_universe": best_universe,
                },
            )
            session_obj.flush()
            return _build_run_report(session_obj, run)
        except Exception as exc:  # noqa: BLE001
            run.status = "failed"
            run.error_message = str(exc)
            run.summary_json = "{}"
            run.candidate_count = 0
            append_audit_event(
                session_obj,
                event_type="discovery_run_failed",
                entity_type="discovery_run",
                entity_id=str(run.id),
                payload={"as_of_date": as_of_date.isoformat(), "error": str(exc)},
            )
            session_obj.flush()
            return _build_run_report(session_obj, run, limit=0)

    if session is not None:
        return _run(session)
    with get_session() as session_obj:
        return _run(session_obj)


def get_latest_discovery_report(
    as_of_date: datetime.date | None = None,
    limit: int = 100,
    session: Session | None = None,
) -> DiscoveryRunReport | None:
    def _get(session_obj: Session) -> DiscoveryRunReport | None:
        Base.metadata.create_all(bind=session_obj.bind)
        stmt = select(DiscoveryRun)
        if as_of_date is not None:
            stmt = stmt.where(DiscoveryRun.as_of_date <= as_of_date)
        run = session_obj.execute(
            stmt.order_by(DiscoveryRun.as_of_date.desc(), DiscoveryRun.created_at.desc())
        ).scalars().first()
        if run is None:
            return None
        return _build_run_report(session_obj, run, limit=limit)

    if session is not None:
        return _get(session)
    with get_session() as session_obj:
        return _get(session_obj)


def get_discovery_watchlist(
    as_of_date: datetime.date,
    limit: int = 5,
    exclude_symbols: set[str] | None = None,
    session: Session | None = None,
) -> list[DiscoveryCandidateItem]:
    exclude = {symbol.upper() for symbol in (exclude_symbols or set())}

    def _has_watchlist_promotion_stability(
        session_obj: Session,
        *,
        ticker: str,
        as_of_date: datetime.date,
        required_runs: int,
        min_score: float,
    ) -> bool:
        if required_runs <= 1:
            return True
        run_ids = session_obj.execute(
            select(DiscoveryRun.id)
            .where(
                DiscoveryRun.status == "succeeded",
                DiscoveryRun.as_of_date <= as_of_date,
            )
            .order_by(DiscoveryRun.as_of_date.desc(), DiscoveryRun.created_at.desc())
            .limit(required_runs)
        ).scalars().all()
        if len(run_ids) < required_runs:
            return False
        for run_id in run_ids:
            score = session_obj.execute(
                select(DiscoveryCandidate.discovery_score).where(
                    DiscoveryCandidate.run_id == run_id,
                    DiscoveryCandidate.ticker == ticker,
                )
            ).scalar_one_or_none()
            if score is None or float(score) < min_score:
                return False
        return True

    def _get(session_obj: Session) -> list[DiscoveryCandidateItem]:
        settings = get_settings()
        latest_run = session_obj.execute(
            select(DiscoveryRun)
            .where(
                DiscoveryRun.status == "succeeded",
                DiscoveryRun.as_of_date <= as_of_date,
            )
            .order_by(DiscoveryRun.as_of_date.desc(), DiscoveryRun.created_at.desc())
        ).scalars().first()
        if latest_run is None:
            return []
        candidate_rows = session_obj.execute(
            select(DiscoveryCandidate)
            .where(DiscoveryCandidate.run_id == latest_run.id)
            .order_by(DiscoveryCandidate.discovery_score.desc(), DiscoveryCandidate.ticker.asc())
        ).scalars().all()
        filtered: list[DiscoveryCandidateItem] = []
        result_limit = max(1, min(limit, settings.discovery_watchlist_limit))
        for row in candidate_rows:
            if row.ticker.upper() in exclude:
                continue
            if bool(row.is_current_holding):
                continue
            if float(row.discovery_score) < settings.discovery_watchlist_promotion_score:
                continue
            if not _has_watchlist_promotion_stability(
                session_obj,
                ticker=row.ticker,
                as_of_date=as_of_date,
                required_runs=settings.discovery_watchlist_min_stable_runs,
                min_score=settings.discovery_watchlist_promotion_score,
            ):
                continue
            filtered.append(
                DiscoveryCandidateItem(
                    ticker=row.ticker,
                    source_universe=row.source_universe,
                    composite_score=float(row.composite_score),
                    discovery_score=float(row.discovery_score),
                    tier=row.tier,
                    rationale=row.rationale,
                    risk_flags=_deserialize_risk_flags(row.risk_flags_json),
                    score_delta=(None if row.score_delta is None else float(row.score_delta)),
                    is_current_holding=bool(row.is_current_holding),
                )
            )
            if len(filtered) >= result_limit:
                break
        return filtered

    if session is not None:
        return _get(session)
    with get_session() as session_obj:
        return _get(session_obj)


__all__ = [
    "run_discovery_scan",
    "get_latest_discovery_report",
    "get_discovery_watchlist",
]
