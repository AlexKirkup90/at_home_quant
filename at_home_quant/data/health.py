from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from at_home_quant.config.settings import get_settings
from at_home_quant.data.tickers import UNIVERSE_BENCHMARK_SYMBOL
from at_home_quant.db.models import PriceDaily, Ticker
from at_home_quant.db.session import get_session

REGIME_BENCHMARK_SYMBOLS = tuple(sorted(set(UNIVERSE_BENCHMARK_SYMBOL.values())))
DEFENSIVE_SYMBOLS = ("BIL", "GLD")
PORTFOLIO_REQUIRED_SYMBOLS = tuple(sorted(set(REGIME_BENCHMARK_SYMBOLS + DEFENSIVE_SYMBOLS)))


@dataclass(frozen=True)
class DataHealthIssue:
    code: str
    message: str


@dataclass
class DataHealthReport:
    as_of_date: datetime.date
    latest_price_date: datetime.date | None
    required_symbols: tuple[str, ...]
    issues: list[DataHealthIssue] = field(default_factory=list)

    @property
    def is_healthy(self) -> bool:
        return not self.issues

    def issue_messages(self) -> list[str]:
        return [issue.message for issue in self.issues]


def _collect_symbol_stats(
    session: Session,
    symbols: Iterable[str],
    as_of_date: datetime.date,
) -> dict[str, tuple[int, datetime.date]]:
    symbol_list = list(symbols)
    if not symbol_list:
        return {}
    rows = session.execute(
        select(
            Ticker.symbol,
            func.count(PriceDaily.id),
            func.max(PriceDaily.date),
        )
        .join(PriceDaily, PriceDaily.ticker_id == Ticker.id)
        .where(
            Ticker.symbol.in_(symbol_list),
            PriceDaily.date <= as_of_date,
        )
        .group_by(Ticker.symbol)
    ).all()
    return {symbol: (int(count), latest_date) for symbol, count, latest_date in rows}


def _build_report(
    session: Session,
    as_of_date: datetime.date | None,
    required_symbols: tuple[str, ...],
) -> DataHealthReport:
    settings = get_settings()
    latest_price_date = session.execute(select(func.max(PriceDaily.date))).scalar_one_or_none()
    effective_as_of = as_of_date or datetime.date.today()
    report = DataHealthReport(
        as_of_date=effective_as_of,
        latest_price_date=latest_price_date,
        required_symbols=required_symbols,
    )

    if latest_price_date is None:
        report.issues.append(
            DataHealthIssue(
                code="no_price_data",
                message="No price data is loaded in the database.",
            )
        )
        return report

    if latest_price_date < effective_as_of:
        lag_days = (effective_as_of - latest_price_date).days
        if lag_days > settings.max_symbol_staleness_days:
            report.issues.append(
                DataHealthIssue(
                    code="database_stale",
                    message=(
                        f"Latest price date {latest_price_date} is {lag_days} days behind "
                        f"requested as-of {effective_as_of}."
                    ),
                )
            )

    stats = _collect_symbol_stats(session, required_symbols, effective_as_of)
    for symbol in required_symbols:
        stat = stats.get(symbol)
        if stat is None:
            report.issues.append(
                DataHealthIssue(
                    code="missing_symbol_history",
                    message=f"No price history available for required symbol {symbol} up to {effective_as_of}.",
                )
            )
            continue

        count, symbol_latest_date = stat
        lag_days = (effective_as_of - symbol_latest_date).days
        if lag_days > settings.max_symbol_staleness_days:
            report.issues.append(
                DataHealthIssue(
                    code="symbol_stale",
                    message=(
                        f"Symbol {symbol} is stale by {lag_days} days "
                        f"(latest {symbol_latest_date}, requested {effective_as_of})."
                    ),
                )
            )

        if symbol in REGIME_BENCHMARK_SYMBOLS and count < settings.min_history_days_for_regime:
            report.issues.append(
                DataHealthIssue(
                    code="insufficient_regime_history",
                    message=(
                        f"Symbol {symbol} has only {count} observations up to {effective_as_of}; "
                        f"requires at least {settings.min_history_days_for_regime}."
                    ),
                )
            )

    return report


def get_data_health_report(
    as_of_date: datetime.date | None = None,
    required_symbols: Iterable[str] | None = None,
    session: Session | None = None,
) -> DataHealthReport:
    symbols = tuple(sorted(set(required_symbols or PORTFOLIO_REQUIRED_SYMBOLS)))
    if session is not None:
        return _build_report(session, as_of_date=as_of_date, required_symbols=symbols)
    with get_session() as session_obj:
        return _build_report(session_obj, as_of_date=as_of_date, required_symbols=symbols)


def assert_data_health_for_portfolio(
    as_of_date: datetime.date,
    session: Session | None = None,
) -> None:
    settings = get_settings()
    if not settings.enforce_data_health_gate:
        return
    report = get_data_health_report(as_of_date=as_of_date, session=session)
    if report.is_healthy:
        return
    details = "; ".join(report.issue_messages())
    raise ValueError(f"Data health gate failed: {details}")


__all__ = [
    "DataHealthIssue",
    "DataHealthReport",
    "REGIME_BENCHMARK_SYMBOLS",
    "PORTFOLIO_REQUIRED_SYMBOLS",
    "get_data_health_report",
    "assert_data_health_for_portfolio",
]
