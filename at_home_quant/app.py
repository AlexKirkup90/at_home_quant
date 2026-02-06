from __future__ import annotations

# Ensure project root is on sys.path for Streamlit execution
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import datetime
import json
import logging
import re
from dataclasses import asdict
from typing import Iterable, Optional

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError

try:  # Streamlit may be optional in some environments
    import streamlit as st
except ImportError as exc:  # pragma: no cover - exercised in runtime, not tests
    st = None  # type: ignore[assignment]
    STREAMLIT_IMPORT_ERROR = exc
else:
    STREAMLIT_IMPORT_ERROR = None

from at_home_quant.config.settings import get_settings
from at_home_quant.backend.service import run_backend_pipeline
from at_home_quant.advisor.models import WorkflowDecisionInput
from at_home_quant.advisor.service import (
    get_latest_advisor_portfolio,
    get_weekly_outcome_report,
    get_latest_weekly_report,
    log_decision,
    save_advisor_portfolio_snapshot,
    save_executed_from_decisions,
)
from at_home_quant.research.service import run_walk_forward_experiment
from at_home_quant.data.tickers import Universe
from at_home_quant.data.health import get_data_health_report
from at_home_quant.db.models import ExperimentRun, PortfolioSnapshot, PriceDaily
from at_home_quant.db.session import get_session, init_db
from at_home_quant.performance.models import MonthlyPerformance, PerformanceSummary
from at_home_quant.performance.stats import compute_performance_summary
from at_home_quant.performance.service import get_monthly_performance
from at_home_quant.portfolio.models import (
    PortfolioRiskReport,
    RebalanceInstruction,
    TargetPortfolio,
    TargetPosition,
)
from at_home_quant.portfolio.service import (
    build_monthly_portfolio,
    compute_rebalance,
    save_manual_portfolio_snapshot,
)
from at_home_quant.regime.models import RegimeDecision, UniverseScore
from at_home_quant.regime.service import get_current_regime
from at_home_quant.selection.service import rank_universe
from at_home_quant.etl.historical_load import run_full_history
from at_home_quant.etl.daily_update import run_daily_update


# ---------- Helpers ----------

def get_latest_price_date() -> Optional[datetime.date]:
    """Return the most recent price date in the database."""
    try:
        with get_session() as session:
            return session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()
    except (OperationalError, SQLAlchemyError) as exc:
        logging.getLogger(__name__).warning("get_latest_price_date failed: %s", exc)
        return None


def get_snapshot_dates() -> list[datetime.date]:
    """Return all available portfolio snapshot dates (descending)."""
    try:
        with get_session() as session:
            dates = session.execute(select(PortfolioSnapshot.as_of_date)).scalars().all()
    except (OperationalError, SQLAlchemyError) as exc:
        logging.getLogger(__name__).warning("get_snapshot_dates failed: %s", exc)
        return []
    return sorted(dates, reverse=True)


def universe_scores_to_dataframe(scores: Iterable[UniverseScore]) -> pd.DataFrame:
    data = [asdict(score) for score in scores]
    if not data:
        return pd.DataFrame()
    return pd.DataFrame(data).drop(columns=["as_of_date"], errors="ignore")


def portfolio_to_dataframe(portfolio: TargetPortfolio) -> pd.DataFrame:
    return pd.DataFrame([asdict(p) for p in portfolio.positions])


def rebalance_to_dataframe(instructions: Iterable[RebalanceInstruction]) -> pd.DataFrame:
    return pd.DataFrame([asdict(inst) for inst in instructions])


def performance_to_dataframe(performance: Iterable[MonthlyPerformance]) -> pd.DataFrame:
    data = [asdict(item) for item in performance]
    return pd.DataFrame(data)


def summary_to_dataframe(summary: PerformanceSummary) -> pd.DataFrame:
    return pd.DataFrame([asdict(summary)])


def risk_report_to_dataframe(report: PortfolioRiskReport | None) -> pd.DataFrame:
    if report is None:
        return pd.DataFrame()
    rows = [asdict(violation) for violation in report.violations]
    if not rows:
        return pd.DataFrame(columns=["code", "message", "current_value", "limit_value"])
    return pd.DataFrame(rows)


def _safe_json_obj(raw: str | None) -> dict:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _format_pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.2f}%"


def _infer_manual_asset_type(ticker: str) -> str:
    symbol = ticker.upper()
    if symbol in {"GLD", "IAU", "SGLN"}:
        return "gold"
    if symbol in {"BIL", "VAGS", "SGOV", "SHY", "AGG", "BND"}:
        return "cash"
    return "equity"


def parse_holdings_text(raw_text: str) -> list[TargetPosition]:
    rows: list[tuple[str, float]] = []
    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        tokens = [token for token in re.split(r"[,\t ]+", line) if token]
        if len(tokens) < 2:
            raise ValueError(f"Line {line_number} is invalid: '{raw_line}'.")
        ticker = tokens[0].upper()
        weight_token = tokens[1].replace("%", "")
        try:
            weight_value = float(weight_token)
        except ValueError as exc:
            raise ValueError(f"Line {line_number} has invalid weight: '{tokens[1]}'.") from exc
        if weight_value < 0:
            raise ValueError(f"Line {line_number} has a negative weight.")
        rows.append((ticker, weight_value))

    if not rows:
        raise ValueError("No holdings were provided.")

    total = sum(weight for _, weight in rows)
    if total > 1.5:
        rows = [(ticker, weight / 100.0) for ticker, weight in rows]
    normalized_total = sum(weight for _, weight in rows)
    if normalized_total <= 0:
        raise ValueError("Holdings total weight must be greater than zero.")
    if abs(normalized_total - 1.0) > 0.01:
        raise ValueError(f"Holdings must sum to 100% (got {normalized_total * 100:.2f}%).")

    return [
        TargetPosition(
            ticker=ticker,
            weight=(weight / normalized_total),
            asset_type=_infer_manual_asset_type(ticker),
        )
        for ticker, weight in rows
    ]


def _decision_default_for_recommendation(recommendation: str) -> str:
    if recommendation in {"buy", "sell"}:
        return "follow"
    return "ignore"


def _portfolio_summary_line(label: str, portfolio: TargetPortfolio | None) -> None:
    require_streamlit()
    if portfolio is None:
        st.caption(f"{label}: not set")
        return
    st.caption(
        f"{label}: {portfolio.as_of_date.isoformat()} | "
        f"{len(portfolio.positions)} positions | "
        f"equity {portfolio.equity_exposure:.2%} / defensive {portfolio.defensive_exposure:.2%}"
    )


def require_streamlit() -> None:
    if st is None:
        raise ImportError(
            "Streamlit is required for the dashboard. Install it with `pip install streamlit`."
        ) from STREAMLIT_IMPORT_ERROR


def _mode_badge_html(mode: str, gate_enabled: bool) -> str:
    if mode.lower() == "production":
        mode_bg = "#7f1d1d"
        mode_label = "PRODUCTION"
    else:
        mode_bg = "#1d4ed8"
        mode_label = "RESEARCH"
    gate_bg = "#14532d" if gate_enabled else "#6b7280"
    gate_label = "HEALTH GATE ON" if gate_enabled else "HEALTH GATE OFF"
    return (
        "<div style='display:flex;gap:8px;justify-content:flex-end;margin-top:8px;'>"
        f"<span style='background:{mode_bg};color:#fff;padding:4px 10px;border-radius:999px;"
        "font-size:12px;font-weight:700;letter-spacing:0.4px;'>"
        f"{mode_label}</span>"
        f"<span style='background:{gate_bg};color:#fff;padding:4px 10px;border-radius:999px;"
        "font-size:12px;font-weight:700;letter-spacing:0.4px;'>"
        f"{gate_label}</span>"
        "</div>"
    )


def show_data_health_panel(as_of_date: datetime.date | None = None) -> None:
    require_streamlit()
    settings = get_settings()
    latest_date = get_latest_price_date()
    effective_as_of = as_of_date or latest_date or datetime.date.today()
    report = get_data_health_report(as_of_date=effective_as_of)

    st.subheader("Data Health")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mode", settings.data_mode.upper())
    col2.metric("Health Gate", "ON" if settings.enforce_data_health_gate else "OFF")
    col3.metric("As-of Date", effective_as_of.isoformat())
    col4.metric("Latest Price Date", latest_date.isoformat() if latest_date else "N/A")
    st.caption("Required symbols: " + ", ".join(report.required_symbols))

    if report.is_healthy:
        st.success("Data health is healthy for the current as-of date.")
    else:
        st.error("Data health has issues that can block portfolio and rebalance operations.")
        issues_df = pd.DataFrame(
            [{"code": issue.code, "message": issue.message} for issue in report.issues]
        )
        st.dataframe(issues_df, use_container_width=True, hide_index=True)


# ---------- UI Sections ----------

def show_weekly_advisor_section() -> None:
    require_streamlit()
    st.header("Weekly Advisor")
    st.caption("Five-step workflow: run backend, confirm holdings, decide, save execution, and review outcomes.")

    latest_price_date = get_latest_price_date()
    if latest_price_date is None:
        st.warning("No price data found. Run Step 1 to initialize backend data.")
        return

    as_of_date = st.date_input(
        "Weekly review date",
        value=latest_price_date,
        max_value=latest_price_date,
        key="weekly_review_date",
    )

    if "weekly_holdings_refresh_required" not in st.session_state:
        st.session_state["weekly_holdings_refresh_required"] = False

    st.subheader("Step 1 — Run Backend")
    st.caption(
        "Runs ETL sync, quality gates, versioned data snapshots, experiment registration, and recommendation generation."
    )
    force_rerun = st.checkbox(
        "Force rerun even if recommendation already exists for this date",
        value=False,
        key="weekly_force_rerun",
    )
    if st.button("Step 1: Run Backend Pipeline", key="weekly_step1"):
        with st.spinner("Running backend pipeline..."):
            try:
                existing_report = get_latest_weekly_report(as_of_date=as_of_date)
                if (
                    existing_report is not None
                    and existing_report.as_of_date == as_of_date
                    and existing_report.experiment_id is not None
                    and not st.session_state.get("weekly_holdings_refresh_required")
                    and not force_rerun
                ):
                    st.info(
                        f"Recommendation batch {existing_report.batch_id} already exists for "
                        f"{as_of_date.isoformat()}. Enable force rerun to rebuild."
                    )
                    st.caption(f"experiment={existing_report.experiment_id}")
                    return
                result = run_backend_pipeline(
                    as_of_date=as_of_date,
                    include_weekly_recommendation=True,
                    retries=2,
                )
                st.session_state["weekly_holdings_refresh_required"] = False
                st.success(
                    f"Backend run {result.run_id} succeeded for {result.as_of_date.isoformat()}."
                )
                st.caption(
                    f"snapshot={result.data_snapshot_hash} | recommendation_batch={result.recommendation_batch_id} "
                    f"| experiment={result.experiment_id}"
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Backend run failed: {exc}")

    st.subheader("Step 2 — Confirm Current Holdings")
    current_executed = get_latest_advisor_portfolio("executed", as_of_date=as_of_date)
    current_baseline = get_latest_advisor_portfolio("baseline", as_of_date=as_of_date)
    _portfolio_summary_line("Latest executed portfolio", current_executed)
    _portfolio_summary_line("Baseline portfolio", current_baseline)

    raw_holdings = st.text_area(
        "Paste ticker and weight (e.g. `AAPL 10%` or `AAPL,10`)",
        value="",
        height=180,
        key="weekly_holdings_input",
    )
    if st.button("Step 2: Save Current Holdings", key="weekly_step2"):
        try:
            positions = parse_holdings_text(raw_holdings)
            if current_baseline is None:
                save_advisor_portfolio_snapshot(
                    as_of_date=as_of_date,
                    positions=positions,
                    snapshot_type="baseline",
                    source="weekly_step2",
                    universe_name="USER_BASELINE",
                )
            save_advisor_portfolio_snapshot(
                as_of_date=as_of_date,
                positions=positions,
                snapshot_type="executed",
                source="weekly_step2",
                universe_name="USER_BASELINE",
            )
            st.success(
                f"Saved current holdings for {as_of_date.isoformat()} ({len(positions)} positions)."
            )
            st.session_state["weekly_holdings_refresh_required"] = True
            st.info("Holdings were updated. Re-run Step 1 before making decisions.")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unable to save current holdings: {exc}")

    report = get_latest_weekly_report(as_of_date=as_of_date)
    if report is None:
        st.info("No weekly recommendation generated yet. Complete Step 1.")
        return

    if st.session_state.get("weekly_holdings_refresh_required"):
        st.warning("Recommendation may be stale versus your latest holdings. Re-run Step 1.")

    st.subheader("Step 3 — Decide (Follow / Ignore / Partial)")
    st.caption(
        f"Batch {report.batch_id} | As-of {report.as_of_date.isoformat()} | "
        f"Best universe {report.best_universe} (score {report.best_universe_score:.2f})"
    )
    pretrade_summary = report.pretrade_summary or {}
    st.markdown("**Pre-Trade Risk & Capacity Check**")
    pcol1, pcol2, pcol3, pcol4 = st.columns(4)
    pcol1.metric("Status", "PASS" if pretrade_summary.get("is_passing", True) else "BLOCKED")
    pcol2.metric("Blocked Trades", str(pretrade_summary.get("blocked_count", 0)))
    pcol3.metric(
        "Expected Shortfall",
        _format_pct(float(pretrade_summary.get("estimated_shortfall_pct", 0.0))),
    )
    pcol4.metric(
        "Max ADV Usage",
        _format_pct(float(pretrade_summary.get("max_adv_participation_seen", 0.0))),
    )
    if report.pretrade_checks:
        checks_df = pd.DataFrame(report.pretrade_checks)
        for column in ["delta", "adv_participation"]:
            if column in checks_df.columns:
                checks_df[column] = checks_df[column].map(
                    lambda value: "" if pd.isna(value) else f"{float(value) * 100:.2f}%"
                )
        for column in ["trade_notional_usd", "adv_usd", "cost_usd"]:
            if column in checks_df.columns:
                checks_df[column] = checks_df[column].map(
                    lambda value: "" if pd.isna(value) else f"${float(value):,.0f}"
                )
        if "total_cost_bps" in checks_df.columns:
            checks_df["total_cost_bps"] = checks_df["total_cost_bps"].map(
                lambda value: "" if pd.isna(value) else f"{float(value):.2f}"
            )
        st.dataframe(checks_df, use_container_width=True, hide_index=True)

    decided_count = sum(1 for item in report.recommendations if item.decision is not None)
    st.caption(f"Decision coverage: {decided_count}/{len(report.recommendations)}")
    recommendation_df = pd.DataFrame(
        [
            {
                "ticker": item.ticker,
                "recommendation": item.recommendation,
                "current_weight": item.current_weight,
                "target_weight": item.target_weight,
                "delta": item.delta,
                "decision": item.decision or "",
                "executed_weight": item.executed_weight,
                "rationale": item.rationale,
            }
            for item in report.recommendations
        ]
    )
    for column in ["current_weight", "target_weight", "delta", "executed_weight"]:
        if column in recommendation_df.columns:
            recommendation_df[column] = recommendation_df[column].map(
                lambda value: "" if pd.isna(value) else f"{float(value) * 100:.2f}%"
            )
    st.dataframe(recommendation_df, use_container_width=True, hide_index=True)

    with st.expander("Log Decisions"):
        for item in report.recommendations:
            st.markdown(f"`{item.ticker}` · recommended **{item.recommendation.upper()}**")
            col1, col2 = st.columns([1, 1])
            default_decision = item.decision or _decision_default_for_recommendation(item.recommendation)
            decision = col1.selectbox(
                f"Decision for {item.ticker}",
                options=["follow", "ignore", "partial"],
                index=["follow", "ignore", "partial"].index(default_decision),
                key=f"decision_{item.id}",
            )
            default_executed = (
                item.executed_weight
                if item.executed_weight is not None
                else (item.target_weight if decision == "follow" else item.current_weight)
            )
            executed_weight = col2.number_input(
                f"Executed weight for {item.ticker}",
                min_value=0.0,
                max_value=1.0,
                value=float(default_executed),
                step=0.001,
                format="%.4f",
                key=f"executed_weight_{item.id}",
            )
            note = st.text_input(
                f"Note for {item.ticker}",
                value=item.note or "",
                key=f"note_{item.id}",
            )
            if st.button(f"Save decision for {item.ticker}", key=f"save_decision_{item.id}"):
                try:
                    log_decision(
                        WorkflowDecisionInput(
                            item_id=item.id,
                            decision=decision,
                            executed_weight=executed_weight if decision == "partial" else None,
                            note=note or None,
                        )
                    )
                    st.success(f"Saved decision for {item.ticker}.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Unable to save decision for {item.ticker}: {exc}")
            st.markdown("---")

    st.subheader("Step 4 — Save Executed Portfolio")
    undecided_count = sum(1 for item in report.recommendations if item.decision is None)
    if undecided_count > 0:
        st.warning(
            f"{undecided_count} recommendations have no explicit decision and will default to IGNORE in Step 4."
        )
    if st.button("Step 4: Save Executed Portfolio From Decisions", key="weekly_step4"):
        try:
            result = save_executed_from_decisions(report.batch_id)
            st.success(
                f"Saved executed portfolio for {result.as_of_date.isoformat()} "
                f"(followed={result.followed_items}, ignored={result.ignored_items}, partial={result.partial_items})."
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unable to save executed portfolio: {exc}")

    st.subheader("Step 5 — Review Outcomes")
    metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
    metrics_col1.metric("Best Universe", report.best_universe)
    metrics_col2.metric("Universe Score", f"{report.best_universe_score:.2f}")
    metrics_col3.metric("Recommendations", str(len(report.recommendations)))
    outcome_counts = {"follow": 0, "ignore": 0, "partial": 0, "unset": 0}
    for item in report.recommendations:
        decision_value = item.decision or "unset"
        outcome_counts[decision_value] = outcome_counts.get(decision_value, 0) + 1
    ocol1, ocol2, ocol3, ocol4 = st.columns(4)
    ocol1.metric("Follow", str(outcome_counts["follow"]))
    ocol2.metric("Ignore", str(outcome_counts["ignore"]))
    ocol3.metric("Partial", str(outcome_counts["partial"]))
    ocol4.metric("Unset", str(outcome_counts["unset"]))

    review_rows = []
    for item in report.recommendations:
        decision = item.decision or "ignore"
        if decision == "follow":
            effective_weight = item.target_weight
        elif decision == "partial":
            midpoint = (item.current_weight + item.target_weight) / 2.0
            effective_weight = item.executed_weight if item.executed_weight is not None else midpoint
        else:
            effective_weight = item.current_weight
        review_rows.append(
            {
                "ticker": item.ticker,
                "recommendation": item.recommendation,
                "decision": item.decision or "",
                "current_weight": item.current_weight,
                "target_weight": item.target_weight,
                "effective_weight": effective_weight,
                "gap_vs_target": effective_weight - item.target_weight,
            }
        )
    review_df = pd.DataFrame(review_rows)
    if review_df.empty:
        st.caption("No recommendation outcomes available yet.")
    else:
        for column in ["current_weight", "target_weight", "effective_weight", "gap_vs_target"]:
            review_df[column] = review_df[column].map(lambda value: f"{float(value) * 100:.2f}%")
        st.dataframe(review_df, use_container_width=True, hide_index=True)

    st.markdown("**Watchlist (near-buys)**")
    if not report.watchlist:
        st.caption("No watchlist candidates for this cycle.")
    else:
        watch_df = pd.DataFrame([asdict(item) for item in report.watchlist])
        st.dataframe(watch_df, use_container_width=True, hide_index=True)

    st.markdown("**Decision Outcome Attribution**")
    outcome_horizon_days = st.slider(
        "Outcome horizon (calendar days)",
        min_value=3,
        max_value=30,
        value=7,
        key="weekly_outcome_horizon_days",
    )
    try:
        outcome_report = get_weekly_outcome_report(
            batch_id=report.batch_id,
            horizon_days=outcome_horizon_days,
        )
    except Exception as exc:  # noqa: BLE001
        outcome_report = None
        st.caption(f"Outcome attribution unavailable: {exc}")

    if outcome_report is None:
        st.caption("Outcome attribution not available yet (insufficient forward price history).")
    else:
        ocol1, ocol2, ocol3, ocol4 = st.columns(4)
        ocol1.metric("Eval Date", outcome_report.evaluation_date.isoformat())
        ocol2.metric("Model Active Return", _format_pct(outcome_report.model_active_return))
        ocol3.metric("Decision Active Return", _format_pct(outcome_report.decision_active_return))
        ocol4.metric("Decision Alpha", _format_pct(outcome_report.decision_alpha))
        scol1, scol2, scol3, scol4 = st.columns(4)
        scol1.metric("Model Shortfall", _format_pct(outcome_report.model_implementation_shortfall))
        scol2.metric("Decision Shortfall", _format_pct(outcome_report.decision_implementation_shortfall))
        scol3.metric("Shortfall Gap", _format_pct(outcome_report.shortfall_gap))
        scol4.metric(
            "Decision vs Benchmark",
            _format_pct(outcome_report.decision_vs_benchmark),
        )
        st.caption(
            f"Follow hit rate: "
            f"{'N/A' if outcome_report.follow_hit_rate is None else f'{outcome_report.follow_hit_rate:.0%}'} | "
            f"Ignored positive opportunities: {outcome_report.ignored_positive_count}"
        )
        outcome_df = pd.DataFrame([asdict(item) for item in outcome_report.items])
        for column in [
            "current_weight",
            "target_weight",
            "effective_weight",
            "forward_return",
            "model_impact",
            "decision_impact",
            "impact_gap",
        ]:
            outcome_df[column] = outcome_df[column].map(lambda value: f"{float(value) * 100:.2f}%")
        st.dataframe(outcome_df, use_container_width=True, hide_index=True)


def show_onboarding_section() -> None:
    require_streamlit()
    st.header("Portfolio Onboarding (Advanced)")
    st.caption(
        "Paste current holdings (ticker + weight) to save your anchor portfolio snapshot. "
        "This becomes the baseline for future rebalance recommendations."
    )

    latest_date = get_latest_price_date() or datetime.date.today()
    snapshot_date = st.date_input(
        "Anchor portfolio date",
        value=latest_date,
        max_value=latest_date,
        key="onboarding_date",
    )
    raw_holdings = st.text_area(
        "Current holdings",
        value="",
        placeholder="AAPL 10%\nMSFT 12.5%\nBIL 20%",
        height=220,
        key="onboarding_holdings",
    )

    if st.button("Save Anchor Portfolio Snapshot", key="save_anchor_snapshot"):
        try:
            positions = parse_holdings_text(raw_holdings)
            save_advisor_portfolio_snapshot(
                as_of_date=snapshot_date,
                positions=positions,
                snapshot_type="baseline",
                source="advanced_onboarding",
                universe_name="USER_BASELINE",
            )
            save_advisor_portfolio_snapshot(
                as_of_date=snapshot_date,
                positions=positions,
                snapshot_type="executed",
                source="advanced_onboarding",
                universe_name="USER_BASELINE",
            )
            portfolio = save_manual_portfolio_snapshot(
                as_of_date=snapshot_date,
                positions=positions,
                universe_name="USER_BASELINE",
            )
            st.success(
                f"Saved anchor portfolio for {snapshot_date.isoformat()} "
                f"({len(portfolio.positions)} positions)."
            )
            st.caption(
                f"Equity exposure: {portfolio.equity_exposure:.2%} | "
                f"Defensive exposure: {portfolio.defensive_exposure:.2%}"
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unable to save anchor portfolio: {exc}")


def show_regime_section(
    as_of_date: datetime.date | None = None,
    read_only: bool = False,
) -> None:
    require_streamlit()
    st.header("Regime & Universe Overview")

    latest_date = get_latest_price_date()
    if latest_date is None:
        st.warning(
            "No price data found in the database. "
            "Run the ETL / data load scripts before using the regime overview."
        )
        return

    if read_only:
        selected_date = min(as_of_date or latest_date, latest_date)
        st.caption(f"As-of date: {selected_date.isoformat()}")
    else:
        selected_date = st.date_input(
            "As-of date",
            value=latest_date,
            max_value=latest_date,
            key="regime_as_of_date",
        )

    try:
        regime: RegimeDecision = get_current_regime(selected_date)
    except Exception as exc:  # noqa: BLE001
        st.warning(
            "Unable to load regime data for the selected date. "
            "Run the ETL and regime scorer first."
        )
        st.caption(str(exc))
        return

    st.subheader("Best universe")
    best_score = next(
        (s for s in regime.all_universe_scores if s.universe_name == regime.best_universe), None
    )
    if best_score:
        col1, col2, col3 = st.columns(3)
        col1.metric("Universe", regime.best_universe)
        col2.metric("Composite score", f"{regime.best_universe_score:.2f}")
        col3.metric(
            "Suggested equity band",
            f"{best_score.suggested_equity_min:.0%} – {best_score.suggested_equity_max:.0%}",
        )

    st.subheader("All universe scores")
    scores_df = universe_scores_to_dataframe(regime.all_universe_scores)
    if scores_df.empty:
        st.info("No universe scores available for this date.")
    else:
        st.dataframe(scores_df, use_container_width=True)


def show_portfolio_section(
    as_of_date: datetime.date | None = None,
    read_only: bool = False,
) -> None:
    require_streamlit()
    st.header("Current Portfolio & Rebalance")

    latest_price_date = get_latest_price_date()
    if latest_price_date is None:
        st.warning(
            "No price data found in the database. "
            "Run the ETL / data load scripts before using the portfolio view."
        )
        return

    snapshot_dates = get_snapshot_dates()
    if snapshot_dates:
        st.caption(f"Latest saved snapshot: {snapshot_dates[0].isoformat()} ({len(snapshot_dates)} total)")
    else:
        st.info("No portfolio snapshots yet. Build and save an initial target portfolio to bootstrap rebalancing.")

    if read_only:
        selected_date = min(as_of_date or latest_price_date, latest_price_date)
        top_n = 15
        threshold = 0.005
        st.caption(
            f"As-of date: {selected_date.isoformat()} | top_n={top_n} | rebalance threshold={threshold:.2%}"
        )
    else:
        selected_date = st.date_input(
            "Portfolio as-of date",
            value=latest_price_date,
            max_value=latest_price_date,
            key="portfolio_as_of_date",
        )
        top_n = st.slider("Top N equities", min_value=1, max_value=50, value=15, key="portfolio_top_n")
        threshold = st.slider("Rebalance threshold (%)", min_value=0.0, max_value=5.0, value=0.5, step=0.1) / 100
    health_report = get_data_health_report(as_of_date=selected_date)
    if not health_report.is_healthy:
        st.error("Data health gate failed for the selected as-of date.")
        for issue in health_report.issue_messages():
            st.caption(f"- {issue}")
        return
    st.caption(f"Data health check passed for {selected_date.isoformat()}.")

    try:
        target_portfolio = build_monthly_portfolio(
            selected_date,
            top_n=top_n,
            persist_snapshot=False,
        )
    except Exception as exc:  # noqa: BLE001
        st.warning("Unable to build target portfolio preview for the selected date.")
        st.caption(str(exc))
        return

    st.subheader("Target portfolio preview")
    portfolio_df = portfolio_to_dataframe(target_portfolio)
    st.dataframe(portfolio_df, use_container_width=True)
    risk_report = target_portfolio.risk_report
    if risk_report is not None:
        st.subheader("Risk overlay")
        rcol1, rcol2, rcol3, rcol4, rcol5 = st.columns(5)
        rcol1.metric("Max position", _format_pct(risk_report.max_position_weight))
        rcol2.metric("Max sector", _format_pct(risk_report.max_sector_weight))
        rcol3.metric("Max region", _format_pct(risk_report.max_region_weight))
        rcol4.metric("Turnover", _format_pct(risk_report.turnover))
        rcol5.metric("Min ADV (USD)", f"{risk_report.min_adv_usd_in_portfolio:,.0f}" if risk_report.min_adv_usd_in_portfolio is not None else "N/A")
        if risk_report.is_within_limits:
            st.success("Risk overlay checks passed.")
        else:
            st.warning("Risk overlay checks have violations.")
            violations_df = risk_report_to_dataframe(risk_report)
            if not violations_df.empty:
                st.dataframe(violations_df, use_container_width=True, hide_index=True)

    if not read_only and st.button("Save Target Snapshot"):
        with st.spinner("Saving target snapshot..."):
            try:
                build_monthly_portfolio(selected_date, top_n=top_n, persist_snapshot=True)
                st.success(f"Saved portfolio snapshot for {selected_date.isoformat()}.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Unable to save portfolio snapshot: {exc}")

    st.subheader("Rebalance instructions (read-only)")
    try:
        instructions = compute_rebalance(selected_date, threshold=threshold, top_n=top_n)
        rebalance_df = rebalance_to_dataframe(instructions)
        st.dataframe(rebalance_df, use_container_width=True)
    except ValueError as exc:
        st.info(
            "No rebalance instructions available yet. "
            "Save a snapshot for an earlier date before rebalancing this date."
        )
        st.caption(str(exc))
    except Exception as exc:  # noqa: BLE001
        st.info("No rebalance instructions available.")
        st.caption(str(exc))


def show_ranking_section(
    as_of_date: datetime.date | None = None,
    read_only: bool = False,
) -> None:
    require_streamlit()
    st.header("Stock Ranking (Equity Sleeve Detail)")

    universes = [u for u in Universe if u != Universe.BENCHMARK]
    latest_date = get_latest_price_date()
    if latest_date is None:
        st.warning(
            "No price data found in the database. "
            "Run the ETL / data load scripts before using the ranking view."
        )
        return

    if read_only:
        selected_date = min(as_of_date or latest_date, latest_date)
        try:
            regime = get_current_regime(selected_date)
            universe_name = Universe[regime.best_universe]
        except Exception:  # noqa: BLE001
            universe_name = Universe.NASDAQ100
        top_n = 15
        st.caption(
            f"As-of date: {selected_date.isoformat()} | universe={universe_name.value} | top_n={top_n}"
        )
    else:
        universe_name = st.selectbox("Universe", options=universes, format_func=lambda u: u.value)
        selected_date = st.date_input(
            "Ranking date",
            value=latest_date,
            max_value=latest_date,
            key="ranking_date",
        )
        top_n = st.slider("Top N", min_value=1, max_value=50, value=15)

    try:
        ranked = rank_universe(universe_name.name, selected_date, top_n=top_n)
    except Exception as exc:  # noqa: BLE001
        st.warning("Unable to load rankings for the selected date.")
        st.caption(str(exc))
        return

    if not ranked:
        st.info("No ranking data available. Populate prices for this universe and date.")
        return

    data = [asdict(r) for r in ranked]
    ranking_df = pd.DataFrame(data)
    st.dataframe(ranking_df, use_container_width=True)


def show_performance_section(read_only: bool = False) -> None:
    require_streamlit()
    st.header("Performance & Alpha")
    settings = get_settings()
    if read_only:
        benchmark_timing = settings.benchmark_selection_timing
        transaction_cost_bps = settings.transaction_cost_bps
        slippage_bps = settings.slippage_bps
    else:
        timing_options = ["period_start", "period_end"]
        default_timing_index = timing_options.index(settings.benchmark_selection_timing)
        col1, col2, col3 = st.columns(3)
        benchmark_timing = col1.selectbox(
            "Benchmark timing",
            options=timing_options,
            index=default_timing_index,
            help="Choose whether benchmark universe selection happens at the start or end of each period.",
        )
        transaction_cost_bps = col2.number_input(
            "Transaction cost (bps)",
            min_value=0.0,
            max_value=100.0,
            value=float(settings.transaction_cost_bps),
            step=0.5,
        )
        slippage_bps = col3.number_input(
            "Slippage (bps)",
            min_value=0.0,
            max_value=100.0,
            value=float(settings.slippage_bps),
            step=0.5,
        )

    try:
        monthly = get_monthly_performance(
            benchmark_timing=benchmark_timing,
            transaction_cost_bps=transaction_cost_bps,
            slippage_bps=slippage_bps,
        )
    except OperationalError:
        st.info(
            "Performance data is unavailable. Build portfolios and run performance calculation first."
        )
        return
    except Exception as exc:  # noqa: BLE001
        st.info("Performance data is unavailable. Build portfolios and run performance calculation first.")
        st.caption(str(exc))
        return

    st.caption(
        "Assumptions: "
        f"benchmark timing={benchmark_timing}, "
        f"transaction cost={transaction_cost_bps:.2f}bps, "
        f"slippage={slippage_bps:.2f}bps."
    )

    monthly_df = performance_to_dataframe(monthly)
    if monthly_df.empty:
        st.info("No performance history available yet. Run at least one monthly portfolio cycle first.")
        return
    summary = compute_performance_summary(monthly)

    st.subheader("Key metrics")
    kpi_col1, kpi_col2, kpi_col3, kpi_col4 = st.columns(4)
    kpi_col1.metric("Net total return", _format_pct(summary.total_return))
    kpi_col2.metric("Gross total return", _format_pct(summary.gross_total_return))
    kpi_col3.metric("Total cost drag", _format_pct(summary.total_transaction_cost))
    kpi_col4.metric(
        "Alpha hit rate",
        f"{summary.positive_alpha_months}/{summary.months} ({_format_pct(summary.alpha_hit_rate)})",
    )

    risk_col1, risk_col2, risk_col3, risk_col4 = st.columns(4)
    risk_col1.metric("CAGR", _format_pct(summary.cagr))
    risk_col2.metric("Volatility", _format_pct(summary.volatility))
    risk_col3.metric("Max drawdown", _format_pct(summary.max_drawdown))
    risk_col4.metric(
        "Information ratio",
        f"{summary.information_ratio:.2f}" if summary.information_ratio is not None else "N/A",
    )

    st.subheader("Monthly performance")
    st.dataframe(monthly_df, use_container_width=True)

    st.subheader("Summary stats")
    summary_df = summary_to_dataframe(summary)
    st.dataframe(summary_df, use_container_width=True)

    if not monthly_df.empty:
        st.subheader("Equity curve vs benchmark")
        perf_chart = monthly_df[
            ["portfolio_return", "portfolio_return_gross", "benchmark_return"]
        ].copy()
        perf_chart["portfolio_equity_net"] = (1 + perf_chart["portfolio_return"]).cumprod()
        perf_chart["portfolio_equity_gross"] = (
            1 + perf_chart["portfolio_return_gross"].fillna(perf_chart["portfolio_return"])
        ).cumprod()
        perf_chart["benchmark_equity"] = (1 + perf_chart["benchmark_return"]).cumprod()
        perf_chart.index = monthly_df["period_end"]
        st.line_chart(perf_chart[["portfolio_equity_net", "portfolio_equity_gross", "benchmark_equity"]])

        st.subheader("Turnover & implementation cost")
        turnover_cost = monthly_df[["portfolio_turnover", "transaction_cost"]].copy()
        turnover_cost.index = monthly_df["period_end"]
        st.line_chart(turnover_cost)

        st.subheader("Alpha over time")
        st.bar_chart(monthly_df.set_index("period_end")["alpha"])


def show_model_governance_section(as_of_date: datetime.date | None = None) -> None:
    require_streamlit()
    st.header("Model Governance")
    latest_price_date = get_latest_price_date()
    effective_as_of = as_of_date or latest_price_date or datetime.date.today()

    with get_session() as session:
        stmt = select(ExperimentRun).order_by(ExperimentRun.created_at.desc())
        if effective_as_of is not None:
            stmt = stmt.where(ExperimentRun.as_of_date <= effective_as_of)
        rows = session.execute(stmt.limit(50)).scalars().all()

    if not rows:
        st.info("No experiment reports found yet.")
        return

    table = []
    for row in rows:
        metrics = _safe_json_obj(row.metrics_json)
        table.append(
            {
                "experiment_id": row.id,
                "created_at": row.created_at.isoformat() if row.created_at else "",
                "run_type": row.run_type,
                "status": row.status,
                "as_of_date": row.as_of_date.isoformat(),
                "snapshot_hash": row.feature_snapshot_hash,
                "leakage_checks_passed": bool(row.leakage_checks_passed),
                "total_return": metrics.get("total_return"),
                "information_ratio": metrics.get("information_ratio"),
            }
        )
    table_df = pd.DataFrame(table)
    st.dataframe(table_df, use_container_width=True, hide_index=True)

    experiment_ids = [row.id for row in rows]
    selected_experiment_id = st.selectbox(
        "Experiment report details",
        options=experiment_ids,
        index=0,
        key="advanced_experiment_id",
    )
    selected = next(row for row in rows if row.id == selected_experiment_id)
    st.caption(
        f"run_type={selected.run_type} | status={selected.status} | "
        f"as_of={selected.as_of_date.isoformat()} | leakage={bool(selected.leakage_checks_passed)}"
    )
    if selected.leakage_message:
        st.caption(f"notes: {selected.leakage_message}")
    metrics = _safe_json_obj(selected.metrics_json)
    challenger = _safe_json_obj(selected.challenger_json)
    robustness = _safe_json_obj(selected.robustness_json)
    col1, col2, col3 = st.columns(3)
    col1.metric("Total Return", _format_pct(metrics.get("total_return")))
    ir = metrics.get("information_ratio")
    col2.metric("Information Ratio", "N/A" if ir is None else f"{float(ir):.2f}")
    outperformance = challenger.get("outperformance_vs_spy")
    col3.metric("Outperf vs SPY", _format_pct(outperformance))
    with st.expander("Metrics JSON"):
        st.json(metrics)
    with st.expander("Challenger JSON"):
        st.json(challenger)
    with st.expander("Robustness JSON"):
        st.json(robustness)


def show_admin_section() -> None:
    require_streamlit()
    st.header("Setup / Admin (Debug)")

    st.write(
        "Use these controls to populate or refresh the local database in this environment. "
        "This is primarily for development and debugging. "
        "Running the historical load may take some time."
    )

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("Run Historical ETL"):
            with st.spinner("Running historical data load..."):
                try:
                    run_full_history()
                    st.success("Historical ETL completed successfully.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Historical ETL failed: {exc}")

    with col2:
        if st.button("Run Daily Update"):
            with st.spinner("Running daily update..."):
                try:
                    run_daily_update()
                    st.success("Daily update completed successfully.")
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Daily update failed: {exc}")

    with col3:
        if st.button("Run Walk-Forward Backtest"):
            with st.spinner("Running walk-forward experiment..."):
                try:
                    result = run_walk_forward_experiment()
                    st.success(
                        f"Experiment {result.experiment_id} completed."
                    )
                    st.caption(
                        "Model report includes challenger comparison and robustness checks. "
                        f"linked_backtest_run={result.linked_run_id}"
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Walk-forward backtest failed: {exc}")


# ---------- Entry point ----------

def main() -> None:
    require_streamlit()
    init_db()
    st.set_page_config(page_title="At-Home Quant Dashboard", layout="wide")
    settings = get_settings()
    title_col, badge_col = st.columns([4, 2])
    with title_col:
        st.title("At-Home Quant Dashboard")
        st.caption("Local-only dashboard for regimes, portfolios, and performance.")
    with badge_col:
        st.markdown(
            _mode_badge_html(settings.data_mode, settings.enforce_data_health_gate),
            unsafe_allow_html=True,
        )

    weekly_tab, advanced_tab = st.tabs(["Weekly Advisor", "Advanced"])

    with weekly_tab:
        show_weekly_advisor_section()

    with advanced_tab:
        latest_price_date = get_latest_price_date()
        if latest_price_date is None:
            st.warning("No price data found. Run backend once from Weekly Advisor to initialize reports.")
        advanced_as_of = st.date_input(
            "Advanced report as-of date",
            value=latest_price_date or datetime.date.today(),
            max_value=latest_price_date,
            key="advanced_as_of_date",
        )
        show_data_health_panel(as_of_date=advanced_as_of)
        st.markdown("---")
        show_regime_section(as_of_date=advanced_as_of, read_only=True)
        st.markdown("---")
        show_portfolio_section(as_of_date=advanced_as_of, read_only=True)
        st.markdown("---")
        show_ranking_section(as_of_date=advanced_as_of, read_only=True)
        st.markdown("---")
        show_performance_section(read_only=True)
        st.markdown("---")
        show_model_governance_section(as_of_date=advanced_as_of)

        if settings.show_debug_admin:
            with st.expander("Developer Tools (Write Actions)"):
                show_onboarding_section()
                st.markdown("---")
                show_admin_section()


if __name__ == "__main__":
    main()
