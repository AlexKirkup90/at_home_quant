import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import at_home_quant.etl.fundamentals_update as fundamentals_module
from at_home_quant.data.tickers import TickerType
from at_home_quant.db.models import Base, FundamentalSnapshot, Ticker


def test_run_fundamentals_update_upserts_scores(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        session.add(
            Ticker(
                symbol="AAA",
                name="AAA",
                asset_type=TickerType.EQUITY,
                universe=None,
                currency="USD",
            )
        )
        session.commit()

    def fake_fetch(symbol: str):
        assert symbol == "AAA"
        return 0.42, 0.15

    monkeypatch.setattr(fundamentals_module, "_fetch_symbol_fundamentals", fake_fetch)

    with Session(engine) as session:
        updated = fundamentals_module.run_fundamentals_update(
            as_of_date=datetime.date(2025, 2, 28),
            symbols=["AAA"],
            session=session,
        )
        assert updated == 1
        row = session.query(FundamentalSnapshot).one()
        assert row.value_score == 0.42
        assert row.shareholder_yield_score == 0.15


def test_run_fundamentals_update_is_best_effort_when_fetch_fails(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with Session(engine) as session:
        session.add(
            Ticker(
                symbol="AAA",
                name="AAA",
                asset_type=TickerType.EQUITY,
                universe=None,
                currency="USD",
            )
        )
        session.add(
            Ticker(
                symbol="BBB",
                name="BBB",
                asset_type=TickerType.EQUITY,
                universe=None,
                currency="USD",
            )
        )
        session.commit()

    def fake_fetch(symbol: str):
        if symbol == "AAA":
            return 0.31, 0.11
        raise RuntimeError("fetch failed")

    monkeypatch.setattr(fundamentals_module, "_fetch_symbol_fundamentals", fake_fetch)

    with Session(engine) as session:
        updated = fundamentals_module.run_fundamentals_update(
            as_of_date=datetime.date(2025, 2, 28),
            symbols=["AAA", "BBB"],
            session=session,
            fail_on_error=False,
        )
        assert updated == 1
        rows = session.query(FundamentalSnapshot).all()
        assert len(rows) == 1
        assert rows[0].value_score == 0.31
