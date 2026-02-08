import datetime
from pathlib import Path
from typing import List, Literal

from pydantic.v1 import BaseSettings, Field


class Settings(BaseSettings):
    app_env: Literal["dev", "stage", "prod"] = Field(
        "dev",
        description="Runtime environment label used for promotion controls and audit context.",
    )
    database_url: str = Field(
        "sqlite:///./data/quant.db",
        description="SQLAlchemy database URL; defaults to local SQLite file.",
    )
    operator_id: str = Field(
        "system",
        description="Operator identity recorded in audit logs.",
    )
    operator_role: Literal["viewer", "analyst", "approver", "admin"] = Field(
        "admin",
        description="Operator role used by release-workflow RBAC checks.",
    )
    enforce_rbac: bool = Field(
        True,
        description="If true, release actions are permission-checked by role.",
    )
    require_release_approval_stage_prod: bool = Field(
        True,
        description="If true, stage/prod model releases must be approved before activation.",
    )
    require_prod_release_controls: bool = Field(
        True,
        description="If true, production backend runs require a recent passed gate artifact and active release.",
    )
    required_active_model_name: str = Field(
        "weekly_quant_v1",
        description="Model name that must have an active release in production runs.",
    )
    prod_gate_max_age_hours: int = Field(
        24,
        description="Maximum age in hours for the latest passed release gate artifact in production.",
    )
    require_gate_code_hash_match: bool = Field(
        True,
        description="If true, production runs require gate artifact code hash to match current code hash.",
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
    risk_max_region_weight: float = Field(
        0.75,
        description="Maximum target aggregate weight for any single equity region.",
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
    execution_portfolio_value_usd: float = Field(
        250_000.0,
        description="Portfolio notional (USD) used for pre-trade capacity and cost estimates.",
    )
    execution_min_ticket_usd: float = Field(
        1_000.0,
        description="Minimum trade ticket size in USD for actionable recommendations.",
    )
    execution_max_adv_participation: float = Field(
        0.10,
        description="Maximum one-day ADV participation allowed for a single trade.",
    )
    execution_impact_bps_at_10pct_adv: float = Field(
        15.0,
        description="Estimated market impact (bps) at 10% ADV participation.",
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
