from __future__ import annotations

# Ensure project root is on sys.path for Streamlit execution
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import datetime
import logging
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

from at_home_quant.data.tickers import Universe
from at_home_quant.db.models import PortfolioSnapshot, PriceDaily
from at_home_quant.db.session import get_session
from at_home_quant.performance.models import MonthlyPerformance, PerformanceSummary
from at_home_quant.performance.service import get_monthly_performance, get_performance_summary
from at_home_quant.portfolio.models import RebalanceInstruction, TargetPortfolio
from at_home_quant.portfolio.service import build_monthly_portfolio, compute_rebalance
from at_home_quant.regime.models import RegimeDecision, UniverseScore
from at_home_quant.regime.service import get_current_regime
from at_home_quant.selection.service import rank_universe
from at_home_quant.etl.historical_load import run_full_history
from at_home_quant.etl.daily_update import run_daily_update


# ---------- Helpers ----------

def get_latest_price_date() -> Optional[datetime.date]:
    """Return the most recent price date in the database."""
    session = get_session()
    if session is None:
        return None
    try:
        return session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()
    except (OperationalError, SQLAlchemyError, AttributeError) as exc:
        logging.getLogger(__name__).warning("get_latest_price_date failed: %s", exc)
        return None
    finally:
        try:
            session.close()
        except AttributeError:
            pass


def get_snapshot_dates() -> list[datetime.date]:
    """Return all available portfolio snapshot dates (descending)."""
    session = get_session()
    if session is None:
        return []
    try:
        dates = session.execute(select(PortfolioSnapshot.as_of_date)).scalars().all()
    except (OperationalError, SQLAlchemyError, AttributeError) as exc:
        logging.getLogger(__name__).warning("get_snapshot_dates failed: %s", exc)
        return []
    finally:
        try:
            session.close()
        except AttributeError:
            pass
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


def require_streamlit() -> None:
    if st is None:
        raise ImportError(
            "Streamlit is required for the dashboard. Install it with `pip install streamlit`."
        ) from STREAMLIT_IMPORT_ERROR


# ---------- UI Sections ----------

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

    snapshot_dates = get_snapshot_dates()
    if not snapshot_dates:
        st.warning(
            "No portfolio snapshots found. "
            "Run the historical ETL and then generate a portfolio in this environment "
            "before using the portfolio/rebalance view."
        )
        return

    latest_price_date = get_latest_price_date()
    default_date = snapshot_dates[0] if snapshot_dates else latest_price_date
    selected_date = st.date_input("Portfolio as-of date", value=default_date, max_value=default_date)
    threshold = st.slider("Rebalance threshold (%)", min_value=0.0, max_value=5.0, value=0.5, step=0.1) / 100

    try:
        target_portfolio = build_monthly_portfolio(selected_date)
    except Exception as exc:  # noqa: BLE001
        st.warning("Unable to build target portfolio for the selected date.")
        st.caption(str(exc))
        return

    st.subheader("Target portfolio")
    portfolio_df = portfolio_to_dataframe(target_portfolio)
    st.dataframe(portfolio_df, use_container_width=True)

    st.subheader("Rebalance instructions")
    try:
        instructions = compute_rebalance(selected_date, threshold=threshold)
        rebalance_df = rebalance_to_dataframe(instructions)
        st.dataframe(rebalance_df, use_container_width=True)
    except Exception as exc:  # noqa: BLE001
        st.info("No rebalance instructions available. Build at least one prior snapshot first.")
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

    try:
        monthly = get_monthly_performance()
        summary = get_performance_summary()
    except OperationalError:
        st.info(
            "Performance data is unavailable. Build portfolios and run performance calculation first."
        )
        return
    except Exception as exc:  # noqa: BLE001
        st.info("Performance data is unavailable. Build portfolios and run performance calculation first.")
        st.caption(str(exc))
        return

    st.subheader("Monthly performance")
    monthly_df = performance_to_dataframe(monthly)
    if monthly_df.empty:
        st.info("No performance history available yet. Run at least one monthly portfolio cycle first.")
        return
    st.dataframe(monthly_df, use_container_width=True)

    st.subheader("Summary stats")
    summary_df = summary_to_dataframe(summary)
    st.dataframe(summary_df, use_container_width=True)

    if not monthly_df.empty:
        st.subheader("Equity curve vs benchmark")
        perf_chart = monthly_df[["portfolio_return", "benchmark_return"]].copy()
        perf_chart["portfolio_equity"] = (1 + perf_chart["portfolio_return"]).cumprod()
        perf_chart["benchmark_equity"] = (1 + perf_chart["benchmark_return"]).cumprod()
        st.line_chart(perf_chart[["portfolio_equity", "benchmark_equity"]])

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

    col1, col2 = st.columns(2)

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


# ---------- Entry point ----------

def main() -> None:
    require_streamlit()
    st.set_page_config(page_title="At-Home Quant Dashboard", layout="wide")
    st.title("At-Home Quant Dashboard")
    st.caption("Local-only dashboard for regimes, portfolios, and performance.")

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
