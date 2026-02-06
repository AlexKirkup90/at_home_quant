import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from at_home_quant.data.tickers import BENCHMARKS, TickerInfo, TickerType
from at_home_quant.db.models import Base, PriceDaily, Ticker
from at_home_quant.portfolio.execution import apply_pretrade_policy, evaluate_pretrade_checks
from at_home_quant.portfolio.models import RebalanceInstruction


def _add_ticker(session: Session, info: TickerInfo) -> int:
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


def _seed_price_history(session: Session, as_of_date: datetime.date) -> None:
    Base.metadata.create_all(bind=session.bind)
    ticker_id = _add_ticker(
        session,
        TickerInfo("TEST", "Test Equity", TickerType.EQUITY, None, "USD"),
    )
    _add_ticker(session, BENCHMARKS["BIL"])
    for idx in range(30):
        day = as_of_date - datetime.timedelta(days=idx)
        session.add(
            PriceDaily(
                ticker_id=ticker_id,
                date=day,
                adj_close=100.0,
                volume=50_000.0,
            )
        )
    session.commit()


def test_pretrade_policy_blocks_capacity_breaches(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    as_of = datetime.date(2026, 2, 6)
    with Session(engine) as session:
        _seed_price_history(session, as_of)
        monkeypatch.setenv("EXECUTION_PORTFOLIO_VALUE_USD", "500000000")
        monkeypatch.setenv("EXECUTION_MAX_ADV_PARTICIPATION", "0.05")
        instructions = [
            RebalanceInstruction(
                ticker="TEST",
                action="buy",
                current_weight=0.0,
                target_weight=0.50,
                delta=0.50,
            )
        ]
        report = evaluate_pretrade_checks(session=session, instructions=instructions, as_of_date=as_of)
        assert not report["is_passing"]
        assert report["blocked_count"] == 1
        gated = apply_pretrade_policy(instructions, report)
        assert gated[0].action == "hold"
        assert gated[0].policy_status == "blocked"
        assert "Pre-trade block" in (gated[0].policy_reason or "")

