from __future__ import annotations

# Ensure project root is on sys.path for Streamlit execution
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import datetime
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
from at_home_quant.advisor.models import WorkflowDecisionInput
from at_home_quant.advisor.service import (
    generate_weekly_recommendation,
    get_latest_advisor_portfolio,
    get_latest_weekly_report,
    log_decision,
    save_advisor_portfolio_snapshot,
    save_executed_from_decisions,
)
from at_home_quant.backtest.service import run_walk_forward_backtest
from at_home_quant.data.tickers import Universe
from at_home_quant.data.health import get_data_health_report
from at_home_quant.db.models import PortfolioSnapshot, PriceDaily
from at_home_quant.db.session import get_session
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


def show_data_health_panel() -> None:
    require_streamlit()
    settings = get_settings()
    latest_date = get_latest_price_date()
    as_of_date = latest_date or datetime.date.today()
    report = get_data_health_report(as_of_date=as_of_date)

    st.subheader("Data Health")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Mode", settings.data_mode.upper())
    col2.metric("Health Gate", "ON" if settings.enforce_data_health_gate else "OFF")
    col3.metric("As-of Date", as_of_date.isoformat())
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
    st.caption("Simple weekly workflow: sync, confirm holdings, generate recommendation, decide, and log execution.")

    latest_price_date = get_latest_price_date()
    if latest_price_date is None:
        st.warning("No price data found. Run Step 1 or the Admin ETL tools first.")
        return

    as_of_date = st.date_input(
        "Weekly review date",
        value=latest_price_date,
        max_value=latest_price_date,
        key="weekly_review_date",
    )

    st.subheader("Step 1 — Sync Data")
    if st.button("Step 1: Sync Data (Daily Update)", key="weekly_step1"):
        with st.spinner("Running daily update..."):
            try:
                run_daily_update()
                st.success("Data sync completed.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Daily update failed: {exc}")

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
            # Keep legacy snapshot table in sync for existing modules.
            save_manual_portfolio_snapshot(
                as_of_date=as_of_date,
                positions=positions,
                universe_name="USER_BASELINE",
            )
            st.success(
                f"Saved current holdings for {as_of_date.isoformat()} ({len(positions)} positions)."
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unable to save current holdings: {exc}")

    st.subheader("Step 3 — Generate Weekly Recommendation")
    if st.button("Step 3: Generate Recommendation", key="weekly_step3"):
        with st.spinner("Generating weekly recommendation..."):
            try:
                report = generate_weekly_recommendation(as_of_date=as_of_date)
                st.success(
                    f"Recommendation batch {report.batch_id} generated for {report.as_of_date.isoformat()}."
                )
            except Exception as exc:  # noqa: BLE001
                st.error(f"Unable to generate recommendation: {exc}")

    report = get_latest_weekly_report(as_of_date=as_of_date)
    if report is None:
        st.info("No weekly recommendation generated yet. Complete Step 3.")
        return

    st.subheader("Step 4 — Decide (Follow / Ignore / Partial)")
    st.caption(
        f"Batch {report.batch_id} | As-of {report.as_of_date.isoformat()} | "
        f"Best universe {report.best_universe} (score {report.best_universe_score:.2f})"
    )
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

    st.subheader("Step 5 — Save Executed Portfolio")
    if st.button("Step 5: Save Executed Portfolio From Decisions", key="weekly_step5"):
        try:
            result = save_executed_from_decisions(report.batch_id)
            # Keep legacy snapshot table in sync for existing modules.
            save_manual_portfolio_snapshot(
                as_of_date=result.as_of_date,
                positions=result.positions,
                universe_name="EXECUTED_FROM_DECISIONS",
            )
            st.success(
                f"Saved executed portfolio for {result.as_of_date.isoformat()} "
                f"(followed={result.followed_items}, ignored={result.ignored_items}, partial={result.partial_items})."
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Unable to save executed portfolio: {exc}")

    st.subheader("Step 6 — Weekly Report")
    metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
    metrics_col1.metric("Best Universe", report.best_universe)
    metrics_col2.metric("Universe Score", f"{report.best_universe_score:.2f}")
    metrics_col3.metric("Recommendations", str(len(report.recommendations)))
    st.markdown("**Watchlist (near-buys)**")
    if not report.watchlist:
        st.caption("No watchlist candidates for this cycle.")
    else:
        watch_df = pd.DataFrame([asdict(item) for item in report.watchlist])
        st.dataframe(watch_df, use_container_width=True, hide_index=True)


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


def show_regime_section() -> None:
    require_streamlit()
    st.header("Regime & Universe Overview")

    latest_date = get_latest_price_date()
    if latest_date is None:
        st.warning(
            "No price data found in the database. "
            "Run the ETL / data load scripts before using the regime overview."
        )
        return

    selected_date = st.date_input("As-of date", value=latest_date, max_value=latest_date)

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


def show_portfolio_section() -> None:
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

    selected_date = st.date_input("Portfolio as-of date", value=latest_price_date, max_value=latest_price_date)
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
        rcol1, rcol2, rcol3, rcol4 = st.columns(4)
        rcol1.metric("Max position", _format_pct(risk_report.max_position_weight))
        rcol2.metric("Max sector", _format_pct(risk_report.max_sector_weight))
        rcol3.metric("Turnover", _format_pct(risk_report.turnover))
        rcol4.metric("Min ADV (USD)", f"{risk_report.min_adv_usd_in_portfolio:,.0f}" if risk_report.min_adv_usd_in_portfolio is not None else "N/A")
        if risk_report.is_within_limits:
            st.success("Risk overlay checks passed.")
        else:
            st.warning("Risk overlay checks have violations.")
            violations_df = risk_report_to_dataframe(risk_report)
            if not violations_df.empty:
                st.dataframe(violations_df, use_container_width=True, hide_index=True)

    if st.button("Save Target Snapshot"):
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


def show_ranking_section() -> None:
    require_streamlit()
    st.header("Stock Ranking (Equity Sleeve Detail)")

    universes = [u for u in Universe if u != Universe.BENCHMARK]
    universe_name = st.selectbox("Universe", options=universes, format_func=lambda u: u.value)
    latest_date = get_latest_price_date()
    if latest_date is None:
        st.warning(
            "No price data found in the database. "
            "Run the ETL / data load scripts before using the ranking view."
        )
        return

    selected_date = st.date_input("Ranking date", value=latest_date, max_value=latest_date, key="ranking_date")
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


def show_performance_section() -> None:
    require_streamlit()
    st.header("Performance & Alpha")
    settings = get_settings()
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
            with st.spinner("Running walk-forward backtest..."):
                try:
                    result = run_walk_forward_backtest()
                    st.success(
                        f"Backtest run {result.run_id} completed with {result.summary.months} monthly periods."
                    )
                    st.caption(
                        "Backtest artifacts saved: "
                        f"code_hash={result.code_hash or 'N/A'}, data_snapshot_hash={result.data_snapshot_hash}"
                    )
                except Exception as exc:  # noqa: BLE001
                    st.error(f"Walk-forward backtest failed: {exc}")


# ---------- Entry point ----------

def main() -> None:
    require_streamlit()
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
        show_data_health_panel()
        st.markdown("---")
        show_onboarding_section()
        st.markdown("---")
        show_regime_section()
        st.markdown("---")
        show_portfolio_section()
        st.markdown("---")
        show_ranking_section()
        st.markdown("---")
        show_performance_section()
        st.markdown("---")
        show_admin_section()


if __name__ == "__main__":
    main()
