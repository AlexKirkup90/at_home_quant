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
    start = datetime.date.today() - datetime.timedelta(days=60)
    df = fetcher.fetch_price_history("BIL", start=start)
    df_returns = fetcher.compute_returns(df)
    assert "return_" in df_returns.columns
    # first return per symbol can be NaN but subsequent should exist if enough data
    non_na_returns = df_returns.dropna(subset=["return_"])
    assert not non_na_returns.empty
    assert df_returns["date"].is_monotonic_increasing
