import datetime
from pathlib import Path
from typing import List, Literal

from pydantic.v1 import BaseSettings, Field


class Settings(BaseSettings):
    database_url: str = Field(
        "sqlite:///./data/quant.db",
        description="SQLAlchemy database URL; defaults to local SQLite file.",
    )
    default_start_date: datetime.date = Field(
        datetime.date(2000, 1, 1), description="Default start date for history fetches"
    )
    data_mode: Literal["research", "production"] = Field(
        "production",
        description="Data behavior mode. 'research' allows synthetic fallback; 'production' fails on missing vendor data.",
    )
    enforce_data_health_gate: bool = Field(
        True,
        description="If true, portfolio construction/rebalance is blocked when data health checks fail.",
    )
    min_history_days_for_regime: int = Field(
        252,
        description="Minimum historical observations required for regime benchmark symbols.",
    )
    max_symbol_staleness_days: int = Field(
        5,
        description="Maximum allowed lag (days) between required symbol data and the requested as-of date.",
    )
    benchmark_selection_timing: Literal["period_start", "period_end"] = Field(
        "period_start",
        description="Date used to choose the benchmark universe for each performance period.",
    )
    transaction_cost_bps: float = Field(
        5.0,
        description="One-way transaction cost in basis points applied to portfolio turnover.",
    )
    slippage_bps: float = Field(
        5.0,
        description="One-way slippage in basis points applied to portfolio turnover.",
    )
    risk_max_position: float = Field(
        0.12,
        description="Maximum target weight allowed for any single position.",
    )
    risk_max_sector_weight: float = Field(
        0.35,
        description="Maximum target aggregate weight for any single equity sector.",
    )
    risk_max_turnover: float = Field(
        0.35,
        description="Maximum monthly portfolio turnover allowed by the risk overlay.",
    )
    risk_min_adv_usd: float = Field(
        5_000_000.0,
        description="Minimum average daily dollar volume required for equity eligibility.",
    )
    risk_adv_lookback_days: int = Field(
        20,
        description="Lookback window (business days) for average daily dollar volume estimation.",
    )
    respect_current_book_mode: bool = Field(
        True,
        description="If true, weekly recommendations preserve current holdings and apply model tilts to sleeves.",
    )
    min_trade_delta_pct: float = Field(
        5.0,
        description="Minimum absolute trade size (%) required to emit a buy/sell recommendation.",
    )
    weight_rounding_pct: float = Field(
        1.0,
        description="Rounding increment (%) for target weights before recommendation generation.",
    )
    enable_trade_gating: bool = Field(
        True,
        description="If true, suppresses low-signal trades using min-size and cost-aware gates.",
    )
    show_debug_admin: bool = Field(
        False,
        description="If true, exposes Advanced tab write/debug controls.",
    )
    benchmark_tickers: List[str] = Field(
        default_factory=lambda: ["QQQ", "SPY", "VMID", "GLD", "IAU", "BIL"],
        description="Default benchmark/asset tickers to fetch",
    )

    class Config:
        env_file = ".env"

    @property
    def allow_synthetic_data(self) -> bool:
        return self.data_mode == "research"


def get_settings() -> Settings:
    return Settings()


def ensure_data_dir_exists(database_url: str) -> None:
    if database_url.startswith("sqlite:///./"):
        data_path = Path(database_url.replace("sqlite:///./", "", 1)).parent
        data_path.mkdir(parents=True, exist_ok=True)


__all__ = ["Settings", "get_settings", "ensure_data_dir_exists"]
