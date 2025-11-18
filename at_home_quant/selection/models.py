from dataclasses import dataclass


@dataclass
class StockFactorScores:
    ticker: str
    momentum_6m: float
    momentum_12m: float
    stability: float
    volatility: float
    value: float
    shareholder_yield: float
    composite_score: float


__all__ = ["StockFactorScores"]
