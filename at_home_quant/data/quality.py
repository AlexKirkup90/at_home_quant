from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class DataQualityIssue:
    code: str
    message: str
    severity: str = "error"  # info|warning|error


@dataclass
class DataQualityReport:
    as_of_date: datetime.date
    row_count: int
    issues: list[DataQualityIssue] = field(default_factory=list)

    @property
    def is_passing(self) -> bool:
        return not any(issue.severity == "error" for issue in self.issues)

    def summary(self) -> str:
        if not self.issues:
            return "No data quality issues."
        return "; ".join(f"{issue.severity}:{issue.code}" for issue in self.issues)


def evaluate_price_quality(
    prices: pd.DataFrame,
    as_of_date: datetime.date,
    max_symbol_staleness_days: int = 5,
) -> DataQualityReport:
    report = DataQualityReport(as_of_date=as_of_date, row_count=len(prices))
    if prices.empty:
        report.issues.append(
            DataQualityIssue("empty_dataset", "Price dataset is empty.", severity="error")
        )
        return report

    required_cols = {"symbol", "date", "close", "adj_close"}
    missing = required_cols - set(prices.columns)
    if missing:
        report.issues.append(
            DataQualityIssue(
                "missing_columns",
                f"Missing required columns: {sorted(missing)}",
                severity="error",
            )
        )
        return report

    missing_close = int(prices["close"].isna().sum())
    missing_adj = int(prices["adj_close"].isna().sum())
    if missing_close > 0 or missing_adj > 0:
        report.issues.append(
            DataQualityIssue(
                "missing_prices",
                f"Missing close/adj_close values: close={missing_close}, adj_close={missing_adj}",
                severity="error",
            )
        )

    prices_local = prices.copy()
    prices_local["date"] = pd.to_datetime(prices_local["date"]).dt.date
    latest_by_symbol = prices_local.groupby("symbol")["date"].max()
    stale = [
        symbol
        for symbol, latest in latest_by_symbol.items()
        if (as_of_date - latest).days > max_symbol_staleness_days
    ]
    if stale:
        report.issues.append(
            DataQualityIssue(
                "stale_symbols",
                f"Stale symbols beyond {max_symbol_staleness_days} days: {', '.join(sorted(stale)[:10])}",
                severity="error",
            )
        )

    prices_local = prices_local.sort_values(["symbol", "date"])
    prices_local["return_"] = prices_local.groupby("symbol")["close"].pct_change()
    extreme_moves = prices_local["return_"].abs() > 0.35
    extreme_count = int(extreme_moves.fillna(False).sum())
    if extreme_count > 0:
        report.issues.append(
            DataQualityIssue(
                "extreme_returns",
                f"Detected {extreme_count} returns above 35% absolute move (possible outliers/splits).",
                severity="warning",
            )
        )

    ratio = prices_local["adj_close"] / prices_local["close"]
    ratio_spikes = ratio.replace([pd.NA, pd.NaT], float("nan")).dropna()
    if not ratio_spikes.empty:
        ratio_outlier_count = int(((ratio_spikes < 0.2) | (ratio_spikes > 5.0)).sum())
        if ratio_outlier_count > 0:
            report.issues.append(
                DataQualityIssue(
                    "adjustment_ratio_outlier",
                    f"Found {ratio_outlier_count} suspicious adj_close/close adjustment ratios.",
                    severity="warning",
                )
            )

    return report


__all__ = ["DataQualityIssue", "DataQualityReport", "evaluate_price_quality"]
