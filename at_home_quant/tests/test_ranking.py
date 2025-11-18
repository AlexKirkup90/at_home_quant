import pandas as pd

from at_home_quant.selection.ranking import (
    DEFAULT_WEIGHTS,
    compute_composite_scores,
    normalize_factors,
    normalize_series,
    rank_stocks,
)


def test_normalization():
    series = pd.Series([1, 2, 3])
    normalized = normalize_series(series)
    assert round(normalized.mean(), 6) == 0
    assert round(normalized.std(ddof=1), 6) == 1


def test_ranking_composite_weights():
    df = pd.DataFrame(
        {
            "ticker": ["A", "B", "C"],
            "momentum": [0.1, 0.2, 0.3],
            "stability": [0.2, 0.1, 0.4],
            "low_volatility": [0.5, 0.4, 0.3],
            "value": [0.01, 0.02, 0.03],
            "shareholder_yield": [0.01, 0.0, 0.04],
        }
    )
    normalized = normalize_factors(df, list(DEFAULT_WEIGHTS.keys()))
    composite = compute_composite_scores(normalized, DEFAULT_WEIGHTS)
    df["composite"] = composite
    ranked = rank_stocks(df, weights=DEFAULT_WEIGHTS)
    assert ranked.iloc[0]["ticker"] == "C"
    assert ranked["composite_score"].is_monotonic_decreasing
