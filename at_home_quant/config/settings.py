import datetime
from pathlib import Path
from typing import List

from pydantic import BaseSettings, Field


class Settings(BaseSettings):
    database_url: str = Field(
        "sqlite:///./data/quant.db",
        description="SQLAlchemy database URL; defaults to local SQLite file.",
    )
    default_start_date: datetime.date = Field(
        datetime.date(2000, 1, 1), description="Default start date for history fetches"
    )
    benchmark_tickers: List[str] = Field(
        default_factory=lambda: ["QQQ", "SPY", "VUKE", "GLD", "IAU", "BIL"],
        description="Default benchmark/asset tickers to fetch",
    )

    class Config:
        env_file = ".env"


def get_settings() -> Settings:
    return Settings()


def ensure_data_dir_exists(database_url: str) -> None:
    if database_url.startswith("sqlite:///./"):
        data_path = Path(database_url.replace("sqlite:///./", "", 1)).parent
        data_path.mkdir(parents=True, exist_ok=True)


__all__ = ["Settings", "get_settings", "ensure_data_dir_exists"]
