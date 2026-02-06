from __future__ import annotations

import datetime
import hashlib
import json

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from at_home_quant.advisor.service import generate_weekly_recommendation
from at_home_quant.backend.models import BackendPipelineResult
from at_home_quant.config.settings import get_settings
from at_home_quant.data.fetcher import compute_returns
from at_home_quant.data.quality import evaluate_price_quality
from at_home_quant.db.models import BackendRun, DataLayerPrice, DatasetSnapshot, PriceDaily, Ticker
from at_home_quant.db.session import get_session, init_db
from at_home_quant.etl.daily_update import run_daily_update
from at_home_quant.etl.fundamentals_update import run_fundamentals_update
from at_home_quant.research.registry import complete_experiment, register_experiment


SQLITE_SAFE_INSERT_BATCH_SIZE = 75


def _load_prices_df(session: Session, as_of_date: datetime.date) -> pd.DataFrame:
    rows = session.execute(
        select(
            Ticker.symbol,
            PriceDaily.date,
            PriceDaily.open,
            PriceDaily.high,
            PriceDaily.low,
            PriceDaily.close,
            PriceDaily.adj_close,
            PriceDaily.volume,
            PriceDaily.return_,
        )
        .join(Ticker, Ticker.id == PriceDaily.ticker_id)
        .where(PriceDaily.date <= as_of_date)
        .order_by(Ticker.symbol, PriceDaily.date)
    ).all()
    if not rows:
        return pd.DataFrame(
            columns=["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "return_"]
        )
    return pd.DataFrame(
        rows,
        columns=["symbol", "date", "open", "high", "low", "close", "adj_close", "volume", "return_"],
    )


def _snapshot_hash(df: pd.DataFrame, layer: str) -> str:
    if df.empty:
        payload = f"{layer}|empty".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
    ordered = df.copy()
    ordered = ordered.sort_values(["symbol", "date"]).reset_index(drop=True)
    payload = ordered.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(layer.encode("utf-8") + b"|" + payload).hexdigest()


def _resolve_as_of_date(session: Session, requested: datetime.date | None) -> datetime.date:
    latest = session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()
    if latest is None:
        raise ValueError("No price data available after ETL run.")
    return requested or latest


def _persist_layer_snapshot(
    session: Session,
    layer: str,
    as_of_date: datetime.date,
    df: pd.DataFrame,
    run_id: int,
) -> DatasetSnapshot:
    snapshot_hash = _snapshot_hash(df, layer)
    existing = session.execute(
        select(DatasetSnapshot).where(
            DatasetSnapshot.layer == layer,
            DatasetSnapshot.snapshot_hash == snapshot_hash,
        )
    ).scalars().first()
    if existing is not None:
        return existing

    snapshot = DatasetSnapshot(
        layer=layer,
        as_of_date=as_of_date,
        snapshot_hash=snapshot_hash,
        row_count=len(df),
        run_id=run_id,
    )
    session.add(snapshot)
    session.flush()

    if df.empty:
        return snapshot

    symbols = sorted(df["symbol"].unique())
    ticker_rows = session.execute(select(Ticker.symbol, Ticker.id).where(Ticker.symbol.in_(symbols))).all()
    symbol_to_id = {symbol: ticker_id for symbol, ticker_id in ticker_rows}
    records = []
    for _, row in df.iterrows():
        ticker_id = symbol_to_id.get(row["symbol"])
        if ticker_id is None:
            continue
        records.append(
            {
                "snapshot_id": snapshot.id,
                "ticker_id": ticker_id,
                "date": row["date"],
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "adj_close": row.get("adj_close"),
                "volume": row.get("volume"),
                "return_": row.get("return_"),
                "layer": layer,
            }
        )

    if records:
        for idx in range(0, len(records), SQLITE_SAFE_INSERT_BATCH_SIZE):
            batch = records[idx : idx + SQLITE_SAFE_INSERT_BATCH_SIZE]
            stmt = sqlite_insert(DataLayerPrice).values(batch)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[DataLayerPrice.snapshot_id, DataLayerPrice.ticker_id, DataLayerPrice.date]
            )
            session.execute(stmt)
    return snapshot


def run_backend_pipeline(
    as_of_date: datetime.date | None = None,
    include_weekly_recommendation: bool = True,
    retries: int = 2,
    top_n: int = 15,
    threshold: float = 0.005,
) -> BackendPipelineResult:
    settings = get_settings()
    init_db()

    with get_session() as session:
        run = BackendRun(status="running", attempts=1)
        session.add(run)
        session.flush()
        run_id = run.id

    last_error: Exception | None = None
    experiment_id: int | None = None
    for attempt in range(1, retries + 2):
        attempt_experiment_id: int | None = None
        try:
            run_daily_update()
            with get_session() as session:
                run = session.execute(select(BackendRun).where(BackendRun.id == run_id)).scalars().one()
                run.attempts = attempt
                resolved_as_of = _resolve_as_of_date(session, as_of_date)
                prices = _load_prices_df(session, resolved_as_of)

                raw_df = prices.copy()
                clean_df = prices.copy()
                if not clean_df.empty:
                    clean_df["adj_close"] = clean_df["adj_close"].fillna(clean_df["close"])
                    clean_df["close"] = clean_df["close"].fillna(clean_df["adj_close"])
                    clean_df = clean_df.dropna(subset=["symbol", "date", "close", "adj_close"])
                run_fundamentals_update(
                    as_of_date=resolved_as_of,
                    symbols=(sorted(clean_df["symbol"].unique()) if not clean_df.empty else None),
                    session=session,
                    fail_on_error=False,
                )
                feature_df = compute_returns(
                    clean_df[["date", "symbol", "open", "high", "low", "close", "adj_close", "volume"]]
                    if not clean_df.empty
                    else clean_df
                )

                quality = evaluate_price_quality(
                    clean_df,
                    as_of_date=resolved_as_of,
                    max_symbol_staleness_days=settings.max_symbol_staleness_days,
                )
                if not quality.is_passing:
                    raise ValueError(f"Data quality gate failed: {quality.summary()}")

                _persist_layer_snapshot(session, "raw", resolved_as_of, raw_df, run_id=run_id)
                _persist_layer_snapshot(session, "clean", resolved_as_of, clean_df, run_id=run_id)
                feature_snapshot = _persist_layer_snapshot(
                    session, "feature", resolved_as_of, feature_df, run_id=run_id
                )

                recommendation_batch_id = None
                if include_weekly_recommendation:
                    experiment = register_experiment(
                        session=session,
                        run_type="backend_weekly",
                        as_of_date=resolved_as_of,
                        feature_snapshot_hash=feature_snapshot.snapshot_hash,
                        params={"top_n": top_n, "threshold": threshold, "retries": retries},
                        window=None,
                    )
                    experiment_id = experiment.id
                    attempt_experiment_id = experiment.id
                    report = generate_weekly_recommendation(
                        as_of_date=resolved_as_of,
                        top_n=top_n,
                        threshold=threshold,
                        data_snapshot_hash=feature_snapshot.snapshot_hash,
                        experiment_id=experiment.id,
                        session=session,
                    )
                    recommendation_batch_id = report.batch_id
                    complete_experiment(
                        session=session,
                        experiment_id=experiment.id,
                        status="succeeded",
                        metrics={
                            "batch_id": report.batch_id,
                            "recommendation_count": len(report.recommendations),
                            "best_universe": report.best_universe,
                            "best_universe_score": report.best_universe_score,
                            "pretrade_blocked_count": report.pretrade_summary.get("blocked_count", 0),
                            "pretrade_shortfall_pct": report.pretrade_summary.get("estimated_shortfall_pct", 0.0),
                        },
                        challenger_comparison={
                            "baseline": "current_holdings",
                            "notes": "Weekly recommendation model is compared against current executed book.",
                        },
                        robustness_checks={
                            "trade_gate_enabled": settings.enable_trade_gating,
                            "pretrade_is_passing": report.pretrade_summary.get("is_passing", True),
                            "max_adv_participation_seen": report.pretrade_summary.get("max_adv_participation_seen", 0.0),
                        },
                    )

                run.status = "succeeded"
                run.finished_at = datetime.datetime.utcnow()
                run.data_snapshot_hash = feature_snapshot.snapshot_hash
                run.message = quality.summary()

                return BackendPipelineResult(
                    run_id=run_id,
                    status=run.status,
                    as_of_date=resolved_as_of,
                    data_snapshot_hash=feature_snapshot.snapshot_hash,
                    quality_summary=quality.summary(),
                    recommendation_batch_id=recommendation_batch_id,
                    experiment_id=experiment_id,
                )
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt_experiment_id is not None:
                with get_session() as session:
                    complete_experiment(
                        session=session,
                        experiment_id=attempt_experiment_id,
                        status="failed",
                        metrics={},
                        challenger_comparison={},
                        robustness_checks={},
                        error_message=str(exc),
                    )
            if attempt <= retries:
                continue

    with get_session() as session:
        run = session.execute(select(BackendRun).where(BackendRun.id == run_id)).scalars().one()
        run.status = "failed"
        run.finished_at = datetime.datetime.utcnow()
        run.message = str(last_error) if last_error else "Unknown backend pipeline failure."
        if experiment_id is not None:
            complete_experiment(
                session=session,
                experiment_id=experiment_id,
                status="failed",
                metrics={},
                challenger_comparison={},
                robustness_checks={},
                error_message=(str(last_error) if last_error else "Unknown backend pipeline failure."),
            )
    if last_error is not None:
        raise last_error
    raise RuntimeError("Backend pipeline failed without exception detail.")


__all__ = ["run_backend_pipeline"]
