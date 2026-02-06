import numpy as np
import pandas as pd
import pytest

from at_home_quant.data.fetcher import compute_returns
from at_home_quant.etl.historical_load import normalize_yfinance_prices


def _build_multiindex_frame(symbol_level_first: bool) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-01", periods=4)
    fields = ["Open", "High", "Low", "Close", "Adj Close", "Volume"]
    symbols = ["AAPL", "MSFT"]
    if symbol_level_first:
        columns = pd.MultiIndex.from_product([symbols, fields])
    else:
        columns = pd.MultiIndex.from_product([fields, symbols])
    values = np.arange(len(dates) * len(columns), dtype=float).reshape(len(dates), len(columns))
    return pd.DataFrame(values, index=dates, columns=columns)


@pytest.mark.parametrize("symbol_level_first", [True, False])
def test_normalize_yfinance_prices_multiticker_supports_returns(symbol_level_first: bool):
    raw = _build_multiindex_frame(symbol_level_first=symbol_level_first)
    normalized = normalize_yfinance_prices(raw)
    computed = compute_returns(normalized)

    assert {"date", "symbol", "close", "adj_close", "return_"}.issubset(computed.columns)
    assert set(computed["symbol"]) == {"AAPL", "MSFT"}
    assert computed.groupby("symbol")["date"].apply(lambda s: s.is_monotonic_increasing).all()
