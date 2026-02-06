from __future__ import annotations

import enum
import datetime
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
    membership_start: datetime.date = datetime.date(1900, 1, 1)
    membership_end: datetime.date | None = None


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

SECTOR_BY_SYMBOL: Dict[str, str] = {
    "AAPL": "Technology",
    "MSFT": "Technology",
    "AMZN": "Consumer Discretionary",
    "GOOGL": "Communication Services",
    "TSCO.L": "Consumer Staples",
    "BVIC.L": "Consumer Staples",
}

REGION_BY_SYMBOL: Dict[str, str] = {
    "TSCO.L": "UK",
    "BVIC.L": "UK",
    "VMID": "UK",
    "VHYL": "UK",
    "VUSA": "US",
    "JGGI": "UK",
    "VAGS": "UK",
    "SGLN": "UK",
}

SYMBOL_EQUIVALENCE_GROUPS: Dict[str, set[str]] = {
    "GLD": {"GLD", "IAU", "SGLN"},
    "BIL": {"BIL", "VAGS", "SGOV", "SHY", "AGG", "BND"},
}

# Internal symbols can differ from vendor symbols; this mapping preserves
# user-facing/internal tickers while trying vendor-specific aliases for fetches.
VENDOR_SYMBOL_ALIASES: Dict[str, tuple[str, ...]] = {
    "JGGI": ("JGGI.L", "JGGI"),
    "VAGS": ("VAGS.L", "VAGP.L", "VAGS"),
    "VHYL": ("VHYL.L", "VHYL.AS", "VHYL"),
    "VUSA": ("VUSA.L", "VUSA.AS", "VUSA"),
    "VMID": ("VMID.L", "VMID"),
}


def sector_for_symbol(symbol: str) -> str:
    return SECTOR_BY_SYMBOL.get(symbol, f"UNKNOWN:{symbol}")


def region_for_symbol(symbol: str) -> str:
    if symbol in REGION_BY_SYMBOL:
        return REGION_BY_SYMBOL[symbol]
    if symbol.endswith(".L"):
        return "UK"
    return "US"


def canonical_symbol(symbol: str) -> str:
    for canonical, group in SYMBOL_EQUIVALENCE_GROUPS.items():
        if symbol in group:
            return canonical
    return symbol


def equivalent_symbols(symbol: str) -> set[str]:
    canonical = canonical_symbol(symbol)
    return SYMBOL_EQUIVALENCE_GROUPS.get(canonical, {canonical})


def vendor_symbol_candidates(symbol: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for candidate in VENDOR_SYMBOL_ALIASES.get(symbol, (symbol,)):
        if candidate in seen:
            continue
        seen.add(candidate)
        ordered.append(candidate)
    if symbol not in seen:
        ordered.append(symbol)
    return ordered


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
    "SECTOR_BY_SYMBOL",
    "REGION_BY_SYMBOL",
    "SYMBOL_EQUIVALENCE_GROUPS",
    "VENDOR_SYMBOL_ALIASES",
    "sector_for_symbol",
    "region_for_symbol",
    "canonical_symbol",
    "equivalent_symbols",
    "vendor_symbol_candidates",
    "list_all_symbols",
    "iter_universe",
]
