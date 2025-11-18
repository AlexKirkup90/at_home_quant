from __future__ import annotations

import pandas as pd


DEFAULT_WEIGHTS = {
    "momentum": 0.40,
    "stability": 0.20,
    "low_volatility": 0.20,
    "value": 0.10,
    "shareholder_yield": 0.10,
}


def normalize_series(values: pd.Series) -> pd.Series:
    mean = values.mean(skipna=True)
    std = values.std(skipna=True)
    if std == 0 or pd.isna(std):
        return pd.Series(0.0, index=values.index)
    return (values - mean) / std


def normalize_factors(df: pd.DataFrame, factor_columns: list[str]) -> pd.DataFrame:
    normalized = pd.DataFrame(index=df.index)
    for col in factor_columns:
        normalized[col] = normalize_series(df[col])
    return normalized


def compute_composite_scores(normalized_df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    weights = weights or DEFAULT_WEIGHTS
    missing = set(weights).difference(normalized_df.columns)
    if missing:
        raise KeyError(f"Missing factors for weights: {missing}")
    composite = pd.Series(0.0, index=normalized_df.index)
    for factor, weight in weights.items():
        composite = composite.add(normalized_df[factor] * weight, fill_value=0.0)
    return composite


def rank_stocks(factor_df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.DataFrame:
    factor_columns = list(weights.keys()) if weights else list(DEFAULT_WEIGHTS.keys())
    normalized = normalize_factors(factor_df, factor_columns)
    composite = compute_composite_scores(normalized, weights)
    result = factor_df.copy()
    result["composite_score"] = composite
    return result.sort_values("composite_score", ascending=False).reset_index(drop=True)


__all__ = [
    "DEFAULT_WEIGHTS",
    "normalize_series",
    "normalize_factors",
    "compute_composite_scores",
    "rank_stocks",
]
