from __future__ import annotations

from sqlalchemy.orm import Session

from at_home_quant.db.session import get_session
from at_home_quant.performance.calc import compute_monthly_performance_series
from at_home_quant.performance.models import MonthlyPerformance, PerformanceSummary
from at_home_quant.performance.stats import compute_performance_summary


def get_monthly_performance(
    session: Session | None = None, regime_getter=None
) -> list[MonthlyPerformance]:
    if regime_getter is None:
        from at_home_quant.regime.service import get_current_regime as regime_getter_default

        regime_getter = regime_getter_default
    if session is not None:
        return compute_monthly_performance_series(session=session, regime_getter=regime_getter)
    with get_session() as session_obj:
        return compute_monthly_performance_series(session=session_obj, regime_getter=regime_getter)


def get_performance_summary(session: Session | None = None, regime_getter=None) -> PerformanceSummary:
    if session is not None:
        series = get_monthly_performance(session=session, regime_getter=regime_getter)
    else:
        series = get_monthly_performance(regime_getter=regime_getter)
    return compute_performance_summary(series)


__all__ = ["get_monthly_performance", "get_performance_summary"]
