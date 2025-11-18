import datetime

import pandas as pd

from at_home_quant.data import fetcher
from at_home_quant.data.tickers import BENCHMARKS


def test_fetch_price_history_has_required_columns():
    start = datetime.date.today() - datetime.timedelta(days=90)
    df = fetcher.fetch_price_history(BENCHMARKS["GLD"], start=start)
    assert set(fetcher.REQUIRED_COLUMNS).issubset(df.columns)
    assert not df.empty
    assert df["date"].is_monotonic_increasing


def test_compute_returns_monotonic_sorting():
    df = pd.DataFrame(
        {
            "date": [
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 2),
                datetime.date(2024, 1, 1),
                datetime.date(2024, 1, 2),
            ],
            "symbol": ["AAA", "AAA", "BBB", "BBB"],
            "open": [100, 101, 200, 202],
            "high": [101, 102, 202, 204],
            "low": [99, 100, 198, 200],
            "close": [100, 102, 200, 206],
            "adj_close": [100, 102, 200, 206],
            "volume": [1_000, 1_100, 2_000, 2_100],
        }
    )

    df_returns = fetcher.compute_returns(df)

    assert "return_" in df_returns.columns
    # ensure dates are sorted within each symbol and returns calculated accordingly
    grouped = df_returns.groupby("symbol")
    for _, group in grouped:
        assert group["date"].is_monotonic_increasing
        assert group.iloc[0]["return_"] == 0.0
        assert group.iloc[1]["return_"] > 0.0
