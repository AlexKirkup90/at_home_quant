import datetime

from at_home_quant.portfolio.models import TargetPortfolio, TargetPosition
from at_home_quant.portfolio.rebalance import diff_portfolios


def _portfolio(as_of: datetime.date, positions: list[TargetPosition]) -> TargetPortfolio:
    return TargetPortfolio(
        as_of_date=as_of,
        positions=positions,
        universe_name="NASDAQ100",
        equity_exposure=sum(p.weight for p in positions if p.asset_type == "equity"),
        defensive_exposure=sum(p.weight for p in positions if p.asset_type != "equity"),
    )


def test_diff_portfolios_actions():
    current = _portfolio(
        datetime.date(2024, 1, 31),
        [
            TargetPosition("AAPL", 0.4, "equity"),
            TargetPosition("MSFT", 0.4, "equity"),
            TargetPosition("BIL", 0.2, "cash"),
        ],
    )
    target = _portfolio(
        datetime.date(2024, 2, 29),
        [
            TargetPosition("AAPL", 0.35, "equity"),
            TargetPosition("MSFT", 0.45, "equity"),
            TargetPosition("GLD", 0.2, "gold"),
        ],
    )

    instructions = diff_portfolios(current, target, threshold=0.01)
    action_map = {instr.ticker: instr.action for instr in instructions}
    assert action_map["AAPL"] == "sell"
    assert action_map["MSFT"] == "buy"
    assert action_map["GLD"] == "buy"
    assert action_map["BIL"] == "sell"
    holds = [instr for instr in instructions if instr.action == "hold"]
    assert all(abs(instr.delta) < 0.01 for instr in holds)
