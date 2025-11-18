import enum
from dataclasses import dataclass
from typing import Dict, Iterable, Mapping


class TickerType(enum.Enum):
    ETF = "ETF"
    INDEX = "INDEX"
    EQUITY = "EQUITY"


class Universe(enum.Enum):
    BENCHMARK = "BENCHMARK"
    NASDAQ100 = "NASDAQ100"
    SP500 = "SP500"
    FTSE250 = "FTSE250"


@dataclass(frozen=True)
class TickerInfo:
    symbol: str
    name: str
    asset_type: TickerType
    universe: Universe | None = None
    currency: str | None = None


BENCHMARKS: Dict[str, TickerInfo] = {
    "QQQ": TickerInfo("QQQ", "Invesco QQQ Trust", TickerType.ETF, Universe.NASDAQ100, "USD"),
    "SPY": TickerInfo("SPY", "SPDR S&P 500 ETF", TickerType.ETF, Universe.SP500, "USD"),
    "VMID": TickerInfo("VMID", "Vanguard FTSE 250 UCITS ETF", TickerType.ETF, Universe.FTSE250, "GBP"),
    "GLD": TickerInfo("GLD", "SPDR Gold Shares", TickerType.ETF, Universe.BENCHMARK, "USD"),
    "IAU": TickerInfo("IAU", "iShares Gold Trust", TickerType.ETF, Universe.BENCHMARK, "USD"),
    "BIL": TickerInfo("BIL", "SPDR Bloomberg 1-3 Month T-Bill ETF", TickerType.ETF, Universe.BENCHMARK, "USD"),
}

# Sample constituent subsets for initial data loads/tests.
SAMPLE_NASDAQ100: Dict[str, TickerInfo] = {
    "AAPL": TickerInfo("AAPL", "Apple Inc.", TickerType.EQUITY, Universe.NASDAQ100, "USD"),
    "MSFT": TickerInfo("MSFT", "Microsoft Corporation", TickerType.EQUITY, Universe.NASDAQ100, "USD"),
}

SAMPLE_SP500: Dict[str, TickerInfo] = {
    "AMZN": TickerInfo("AMZN", "Amazon.com Inc.", TickerType.EQUITY, Universe.SP500, "USD"),
    "GOOGL": TickerInfo("GOOGL", "Alphabet Inc. Class A", TickerType.EQUITY, Universe.SP500, "USD"),
}

SAMPLE_FTSE250: Dict[str, TickerInfo] = {
    "TSCO.L": TickerInfo("TSCO.L", "Tesco PLC", TickerType.EQUITY, Universe.FTSE250, "GBP"),
    "BVIC.L": TickerInfo("BVIC.L", "Britvic PLC", TickerType.EQUITY, Universe.FTSE250, "GBP"),
}

ALL_TICKERS: Dict[str, TickerInfo] = {
    **BENCHMARKS,
    **SAMPLE_NASDAQ100,
    **SAMPLE_SP500,
    **SAMPLE_FTSE250,
}

UNIVERSE_BENCHMARK_SYMBOL: Dict[Universe, str] = {
    Universe.NASDAQ100: "QQQ",
    Universe.SP500: "SPY",
    Universe.FTSE250: "VMID",
}


def list_all_symbols() -> list[str]:
    return list(ALL_TICKERS.keys())


def iter_universe(universe: Universe) -> Iterable[TickerInfo]:
    return (info for info in ALL_TICKERS.values() if info.universe == universe)


__all__ = [
    "TickerType",
    "Universe",
    "TickerInfo",
    "BENCHMARKS",
    "SAMPLE_NASDAQ100",
    "SAMPLE_SP500",
    "SAMPLE_FTSE250",
    "ALL_TICKERS",
    "UNIVERSE_BENCHMARK_SYMBOL",
    "list_all_symbols",
    "iter_universe",
]
