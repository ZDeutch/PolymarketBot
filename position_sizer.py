"""Calculates optimal stake per outcome given a confirmed arbitrage opportunity, normalized to a $10,000 simulated bankroll."""

import math


def calculate_stakes(prices: dict, cycle_sum: float, bankroll: float) -> dict:
    stakes = {outcome: round(price * bankroll, 2) for outcome, price in prices.items()}
    total_deployed = round(cycle_sum * bankroll, 2)
    expected_payout = round(bankroll, 2)
    expected_profit = round(bankroll * (1 - cycle_sum), 2)

    return {
        "stakes": stakes,
        "total_deployed": total_deployed,
        "expected_payout": expected_payout,
        "expected_profit": expected_profit,
    }


