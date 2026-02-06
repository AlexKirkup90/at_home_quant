import datetime
import json
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import at_home_quant.app as app_module
from at_home_quant.data.tickers import BENCHMARKS
from at_home_quant.db.models import Base, PortfolioSnapshot, PriceDaily, Ticker
from at_home_quant.regime.models import RegimeDecision, UniverseScore


def _add_ticker(session: Session, symbol: str) -> int:
    info = BENCHMARKS[symbol]
    ticker = Ticker(
        symbol=info.symbol,
        name=info.name,
        asset_type=info.asset_type,
        universe=info.universe,
        currency=info.currency,
    )
    session.add(ticker)
    session.flush()
    return ticker.id


def test_get_latest_price_date_returns_database_max(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as seed_session:
        ticker_id = _add_ticker(seed_session, "QQQ")
        seed_session.add(PriceDaily(ticker_id=ticker_id, date=datetime.date(2025, 1, 30), adj_close=100.0))
        seed_session.add(PriceDaily(ticker_id=ticker_id, date=datetime.date(2025, 1, 31), adj_close=101.0))
        seed_session.commit()

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(app_module, "get_session", get_session_override)
    assert app_module.get_latest_price_date() == datetime.date(2025, 1, 31)


def test_get_snapshot_dates_returns_descending(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as seed_session:
        seed_session.add(
            PortfolioSnapshot(
                as_of_date=datetime.date(2025, 1, 31),
                universe_name="NASDAQ100",
                equity_exposure=0.8,
                defensive_exposure=0.2,
                positions_json=json.dumps([{"ticker": "QQQ", "weight": 1.0, "asset_type": "equity"}]),
            )
        )
        seed_session.add(
            PortfolioSnapshot(
                as_of_date=datetime.date(2024, 12, 31),
                universe_name="NASDAQ100",
                equity_exposure=0.8,
                defensive_exposure=0.2,
                positions_json=json.dumps([{"ticker": "QQQ", "weight": 1.0, "asset_type": "equity"}]),
            )
        )
        seed_session.commit()

    @contextmanager
    def get_session_override():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(app_module, "get_session", get_session_override)
    assert app_module.get_snapshot_dates() == [datetime.date(2025, 1, 31), datetime.date(2024, 12, 31)]


class _DummyColumn:
    def __init__(self, parent):
        self._parent = parent

    def metric(self, *args, **kwargs):
        self._parent.metrics.append((args, kwargs))


class _DummyStreamlit:
    def __init__(self):
        self.warnings: list[str] = []
        self.metrics: list[tuple] = []
        self.dataframes = []

    def header(self, *_args, **_kwargs):
        return None

    def subheader(self, *_args, **_kwargs):
        return None

    def date_input(self, _label, value=None, **_kwargs):
        return value

    def columns(self, count):
        return [_DummyColumn(self) for _ in range(count)]

    def dataframe(self, df, **_kwargs):
        self.dataframes.append(df)

    def warning(self, message):
        self.warnings.append(message)

    def info(self, *_args, **_kwargs):
        return None

    def caption(self, *_args, **_kwargs):
        return None


def test_show_regime_section_renders_when_data_exists(monkeypatch):
    as_of = datetime.date(2025, 1, 31)
    score = UniverseScore(
        as_of_date=as_of,
        universe_name="NASDAQ100",
        composite_score=72.0,
        trend=0.1,
        momentum_6m=0.08,
        momentum_12m=0.15,
        momentum_rank=1,
        realized_vol=0.2,
        drawdown=-0.05,
        suggested_equity_min=0.7,
        suggested_equity_max=0.9,
    )
    regime = RegimeDecision(
        as_of_date=as_of,
        best_universe="NASDAQ100",
        best_universe_score=72.0,
        all_universe_scores=[score],
    )
    fake_streamlit = _DummyStreamlit()

    monkeypatch.setattr(app_module, "st", fake_streamlit)
    monkeypatch.setattr(app_module, "get_latest_price_date", lambda: as_of)
    monkeypatch.setattr(app_module, "get_current_regime", lambda _date: regime)

    app_module.show_regime_section()

    assert not fake_streamlit.warnings
    assert fake_streamlit.metrics
    assert fake_streamlit.dataframes
