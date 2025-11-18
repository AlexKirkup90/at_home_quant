from __future__ import annotations

from typing import List

from at_home_quant.portfolio.models import RebalanceInstruction, TargetPortfolio


def diff_portfolios(
    current: TargetPortfolio, target: TargetPortfolio, threshold: float = 0.005
) -> List[RebalanceInstruction]:
    current_map = {p.ticker: p.weight for p in current.positions}
    target_map = {p.ticker: p.weight for p in target.positions}
    all_tickers = set(current_map) | set(target_map)

    instructions: list[RebalanceInstruction] = []
    for ticker in sorted(all_tickers):
        cur_weight = current_map.get(ticker, 0.0)
        tgt_weight = target_map.get(ticker, 0.0)
        delta = tgt_weight - cur_weight
        if abs(delta) < threshold:
            action = "hold"
        elif delta > 0:
            action = "buy"
        else:
            action = "sell"
        instructions.append(
            RebalanceInstruction(
                ticker=ticker,
                action=action,
                current_weight=cur_weight,
                target_weight=tgt_weight,
                delta=delta,
            )
        )
    return instructions


__all__ = ["diff_portfolios"]
