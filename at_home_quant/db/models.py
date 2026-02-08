import datetime

from sqlalchemy import Column, Date, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
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
    memberships = relationship("UniverseMembership", back_populates="ticker")


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


class UniverseMembership(Base):
    __tablename__ = "universe_memberships"
    __table_args__ = (
        UniqueConstraint("ticker_id", "universe", "effective_from", name="uq_universe_membership"),
    )

    id = Column(Integer, primary_key=True)
    ticker_id = Column(Integer, ForeignKey("tickers.id"), nullable=False, index=True)
    universe = Column(Enum(Universe), nullable=False, index=True)
    effective_from = Column(Date, nullable=False, index=True)
    effective_to = Column(Date, nullable=True, index=True)

    ticker = relationship("Ticker", back_populates="memberships")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    start_date = Column(Date, nullable=False, index=True)
    end_date = Column(Date, nullable=False, index=True)
    code_hash = Column(String, nullable=True)
    data_snapshot_hash = Column(String, nullable=False)
    config_json = Column(Text, nullable=False)
    summary_json = Column(Text, nullable=False)
    monthly_results_json = Column(Text, nullable=False)


class FundamentalSnapshot(Base):
    __tablename__ = "fundamental_snapshots"
    __table_args__ = (UniqueConstraint("ticker_id", "as_of_date", name="uq_fundamental_ticker_date"),)

    id = Column(Integer, primary_key=True)
    ticker_id = Column(Integer, ForeignKey("tickers.id"), nullable=False, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    value_score = Column(Float, nullable=True)
    shareholder_yield_score = Column(Float, nullable=True)


class AdvisorPortfolioSnapshot(Base):
    __tablename__ = "advisor_portfolio_snapshots"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    snapshot_type = Column(String, nullable=False, index=True)  # baseline|executed|model_target
    source = Column(String, nullable=False, default="app", index=True)
    universe_name = Column(String, nullable=False)
    equity_exposure = Column(Float, nullable=False)
    defensive_exposure = Column(Float, nullable=False)
    positions_json = Column(Text, nullable=False)


class WeeklyRecommendationBatch(Base):
    __tablename__ = "weekly_recommendation_batches"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    as_of_date = Column(Date, nullable=False, index=True)
    best_universe = Column(String, nullable=False)
    best_universe_score = Column(Float, nullable=False)
    status = Column(String, nullable=False, default="open", index=True)  # open|closed
    data_snapshot_hash = Column(String, nullable=True, index=True)
    watchlist_json = Column(Text, nullable=False, default="[]")


class WeeklyRecommendationItem(Base):
    __tablename__ = "weekly_recommendation_items"

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("weekly_recommendation_batches.id"), nullable=False, index=True)
    ticker = Column(String, nullable=False, index=True)
    recommendation = Column(String, nullable=False)  # buy|sell|hold
    current_weight = Column(Float, nullable=False)
    target_weight = Column(Float, nullable=False)
    delta = Column(Float, nullable=False)
    rationale = Column(Text, nullable=False)


class RecommendationDecision(Base):
    __tablename__ = "recommendation_decisions"
    __table_args__ = (UniqueConstraint("item_id", name="uq_recommendation_decision_item"),)

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    item_id = Column(Integer, ForeignKey("weekly_recommendation_items.id"), nullable=False, index=True)
    decision = Column(String, nullable=False)  # follow|ignore|partial
    executed_weight = Column(Float, nullable=True)
    note = Column(Text, nullable=True)


class BackendRun(Base):
    __tablename__ = "backend_runs"

    id = Column(Integer, primary_key=True)
    started_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    finished_at = Column(DateTime, nullable=True, index=True)
    status = Column(String, nullable=False, default="running", index=True)  # running|succeeded|failed
    attempts = Column(Integer, nullable=False, default=1)
    message = Column(Text, nullable=True)
    data_snapshot_hash = Column(String, nullable=True, index=True)


class DatasetSnapshot(Base):
    __tablename__ = "dataset_snapshots"
    __table_args__ = (
        UniqueConstraint("layer", "snapshot_hash", name="uq_dataset_layer_hash"),
    )

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    layer = Column(String, nullable=False, index=True)  # raw|clean|feature
    as_of_date = Column(Date, nullable=False, index=True)
    snapshot_hash = Column(String, nullable=False, index=True)
    row_count = Column(Integer, nullable=False)
    run_id = Column(Integer, ForeignKey("backend_runs.id"), nullable=True, index=True)


class DataLayerPrice(Base):
    __tablename__ = "data_layer_prices"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "ticker_id", "date", name="uq_layer_price_snapshot_ticker_date"),
    )

    id = Column(Integer, primary_key=True)
    snapshot_id = Column(Integer, ForeignKey("dataset_snapshots.id"), nullable=False, index=True)
    ticker_id = Column(Integer, ForeignKey("tickers.id"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    open = Column(Float, nullable=True)
    high = Column(Float, nullable=True)
    low = Column(Float, nullable=True)
    close = Column(Float, nullable=True)
    adj_close = Column(Float, nullable=False)
    volume = Column(Float, nullable=True)
    return_ = Column(Float, nullable=True)
    layer = Column(String, nullable=False, index=True)


class ExperimentRun(Base):
    __tablename__ = "experiment_runs"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    run_type = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False, default="running", index=True)  # running|succeeded|failed
    as_of_date = Column(Date, nullable=False, index=True)
    feature_snapshot_hash = Column(String, nullable=False, index=True)
    params_json = Column(Text, nullable=False, default="{}")
    code_hash = Column(String, nullable=True)
    train_start = Column(Date, nullable=True, index=True)
    train_end = Column(Date, nullable=True, index=True)
    validation_start = Column(Date, nullable=True, index=True)
    validation_end = Column(Date, nullable=True, index=True)
    holdout_start = Column(Date, nullable=True, index=True)
    holdout_end = Column(Date, nullable=True, index=True)
    leakage_checks_passed = Column(Integer, nullable=False, default=0)  # 0|1 for SQLite portability
    leakage_message = Column(Text, nullable=True)
    metrics_json = Column(Text, nullable=False, default="{}")
    challenger_json = Column(Text, nullable=False, default="{}")
    robustness_json = Column(Text, nullable=False, default="{}")
    artifact_path = Column(String, nullable=True)


class WeeklyRecommendationExperimentLink(Base):
    __tablename__ = "weekly_recommendation_experiment_links"
    __table_args__ = (UniqueConstraint("batch_id", name="uq_weekly_batch_experiment"),)

    id = Column(Integer, primary_key=True)
    batch_id = Column(Integer, ForeignKey("weekly_recommendation_batches.id"), nullable=False, index=True)
    experiment_id = Column(Integer, ForeignKey("experiment_runs.id"), nullable=False, index=True)


class BacktestExperimentLink(Base):
    __tablename__ = "backtest_experiment_links"
    __table_args__ = (UniqueConstraint("backtest_run_id", name="uq_backtest_run_experiment"),)

    id = Column(Integer, primary_key=True)
    backtest_run_id = Column(Integer, ForeignKey("backtest_runs.id"), nullable=False, index=True)
    experiment_id = Column(Integer, ForeignKey("experiment_runs.id"), nullable=False, index=True)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    actor = Column(String, nullable=False, index=True)
    environment = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=True, index=True)
    entity_id = Column(String, nullable=True, index=True)
    payload_json = Column(Text, nullable=False, default="{}")
    prev_hash = Column(String, nullable=True, index=True)
    event_hash = Column(String, nullable=False, unique=True, index=True)


class ModelRelease(Base):
    __tablename__ = "model_releases"

    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow, index=True)
    model_name = Column(String, nullable=False, index=True)
    environment = Column(String, nullable=False, index=True)
    experiment_id = Column(Integer, ForeignKey("experiment_runs.id"), nullable=False, index=True)
    status = Column(String, nullable=False, default="proposed", index=True)
    proposed_by = Column(String, nullable=False, index=True)
    approved_by = Column(String, nullable=True, index=True)
    approved_at = Column(DateTime, nullable=True, index=True)
    activated_at = Column(DateTime, nullable=True, index=True)
    deactivated_at = Column(DateTime, nullable=True, index=True)
    notes = Column(Text, nullable=True)


__all__ = [
    "Base",
    "Ticker",
    "PriceDaily",
    "PortfolioSnapshot",
    "UniverseMembership",
    "BacktestRun",
    "FundamentalSnapshot",
    "AdvisorPortfolioSnapshot",
    "WeeklyRecommendationBatch",
    "WeeklyRecommendationItem",
    "RecommendationDecision",
    "BackendRun",
    "DatasetSnapshot",
    "DataLayerPrice",
    "ExperimentRun",
    "WeeklyRecommendationExperimentLink",
    "BacktestExperimentLink",
    "AuditEvent",
    "ModelRelease",
]
