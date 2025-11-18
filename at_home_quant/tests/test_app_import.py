import datetime

from at_home_quant.app import portfolio_to_dataframe, rebalance_to_dataframe
from at_home_quant.portfolio.models import RebalanceInstruction, TargetPortfolio, TargetPosition


def test_portfolio_to_dataframe():
    portfolio = TargetPortfolio(
        as_of_date=datetime.date(2025, 1, 31),
        universe_name="NASDAQ100",
        equity_exposure=0.6,
        defensive_exposure=0.4,
        positions=[
            TargetPosition(ticker="AAPL", weight=0.3, asset_type="equity"),
            TargetPosition(ticker="GLD", weight=0.7, asset_type="gold"),
        ],
    )

    df = portfolio_to_dataframe(portfolio)

    assert not df.empty
    assert set(df.columns) == {"ticker", "weight", "asset_type"}
    assert df.loc[df["ticker"] == "AAPL", "weight"].iloc[0] == 0.3


def test_rebalance_to_dataframe():
    instructions = [
        RebalanceInstruction(
            ticker="AAPL", action="buy", current_weight=0.2, target_weight=0.3, delta=0.1
        ),
        RebalanceInstruction(
            ticker="GLD", action="sell", current_weight=0.8, target_weight=0.7, delta=-0.1
        ),
    ]

    df = rebalance_to_dataframe(instructions)

    assert not df.empty
    assert set(df.columns) == {"ticker", "action", "current_weight", "target_weight", "delta"}
    assert df.shape[0] == 2
