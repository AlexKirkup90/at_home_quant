from sqlalchemy import Column, Date, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship

from at_home_quant.data.tickers import TickerType, Universe

Base = declarative_base()


class Ticker(Base):
    __tablename__ = "tickers"

    id = Column(Integer, primary_key=True)
    symbol = Column(String, unique=True, nullable=False, index=True)
    name = Column(String, nullable=False)
    asset_type = Column(Enum(TickerType), nullable=False)
    universe = Column(Enum(Universe), nullable=True)
    currency = Column(String, nullable=True)

    prices = relationship("PriceDaily", back_populates="ticker")


class PriceDaily(Base):
    __tablename__ = "prices_daily"
    __table_args__ = (UniqueConstraint("ticker_id", "date", name="uq_prices_ticker_date"),)

    id = Column(Integer, primary_key=True)
    ticker_id = Column(Integer, ForeignKey("tickers.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    adj_close = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)
    return_ = Column(Float, nullable=True)

    ticker = relationship("Ticker", back_populates="prices")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("as_of_date", name="uq_portfolio_as_of_date"),)

    id = Column(Integer, primary_key=True)
    as_of_date = Column(Date, nullable=False, index=True)
    universe_name = Column(String, nullable=False)
    equity_exposure = Column(Float, nullable=False)
    defensive_exposure = Column(Float, nullable=False)
    positions_json = Column(Text, nullable=False)


__all__ = ["Base", "Ticker", "PriceDaily", "PortfolioSnapshot"]
