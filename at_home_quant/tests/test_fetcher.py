import datetime

import numpy as np
import pandas as pd
import pytest

from at_home_quant.data import fetcher
from at_home_quant.data.tickers import BENCHMARKS


def test_fetch_price_history_has_required_columns(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "research")
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


@pytest.mark.parametrize("symbol_level_first", [True, False])
def test_normalize_df_handles_single_symbol_multiindex(symbol_level_first: bool):
    symbol = "QQQ"
    dates = pd.bdate_range("2025-01-01", periods=4)
    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    if symbol_level_first:
        columns = pd.MultiIndex.from_product([[symbol], fields])
    else:
        columns = pd.MultiIndex.from_product([fields, [symbol]])
    values = np.arange(len(dates) * len(columns), dtype=float).reshape(len(dates), len(columns))
    raw = pd.DataFrame(values, index=dates, columns=columns)

    normalized = fetcher._normalize_df(raw, symbol)

    assert set(fetcher.REQUIRED_COLUMNS).issubset(normalized.columns)
    assert set(normalized["symbol"]) == {symbol}
    assert normalized["date"].is_monotonic_increasing


def test_fetch_price_history_production_mode_raises_on_empty_download(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "production")
    monkeypatch.setattr(fetcher.yf, "download", lambda *args, **kwargs: pd.DataFrame())

    with pytest.raises(RuntimeError, match="No market data returned"):
        fetcher.fetch_price_history("QQQ", start=datetime.date(2025, 1, 1))


def test_fetch_price_history_research_mode_uses_synthetic_on_empty_download(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "research")
    monkeypatch.setattr(fetcher.yf, "download", lambda *args, **kwargs: pd.DataFrame())

    df = fetcher.fetch_price_history("QQQ", start=datetime.date(2025, 1, 1))
    assert not df.empty
    assert set(fetcher.REQUIRED_COLUMNS).issubset(df.columns)


def test_fetch_price_history_uses_vendor_alias_candidates(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "production")
    dates = pd.bdate_range("2025-01-01", periods=3)
    raw = pd.DataFrame(
        {
            "Open": [100.0, 101.0, 102.0],
            "High": [101.0, 102.0, 103.0],
            "Low": [99.0, 100.0, 101.0],
            "Close": [100.5, 101.5, 102.5],
            "Adj Close": [100.5, 101.5, 102.5],
            "Volume": [1_000_000, 1_000_000, 1_000_000],
        },
        index=dates,
    )

    calls: list[str] = []

    def download_override(symbol, *args, **kwargs):  # noqa: ARG001
        calls.append(symbol)
        if symbol == "VUSA.L":
            return raw
        return pd.DataFrame()

    monkeypatch.setattr(fetcher.yf, "download", download_override)
    df = fetcher.fetch_price_history("VUSA", start=datetime.date(2025, 1, 1))
    assert not df.empty
    assert set(df["symbol"]) == {"VUSA"}
    assert calls == ["VUSA.L"]
