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


if __name__ == "__main__":
    # Test 1: 3-outcome arb opportunity
    # cycle_sum = 0.90, bankroll = 10000
    # expected: total_deployed = $9000, profit = $1000
    test_prices_1 = {
        "Le Pen wins": 0.42,
        "Macron wins": 0.38,
        "Other wins": 0.10
    }
    result1 = calculate_stakes(test_prices_1, 0.90, 10000.0)
    print("Test 1:")
    print("  Stakes:", result1["stakes"])
    print("  Total deployed:", result1["total_deployed"])
    print("  Expected payout:", result1["expected_payout"])
    print("  Expected profit:", result1["expected_profit"])
    print()

    # Test 2: verify payout is equal for every outcome
    # For each outcome: stake / price should equal bankroll
    print("Test 2 — payout verification:")
    for outcome, stake in result1["stakes"].items():
        price = test_prices_1[outcome]
        payout = round(stake / price, 2)
        print(f"  If '{outcome}' wins: spent ${stake}, collect ${payout}")
    print()

    # Test 3: 2-outcome race
    # cycle_sum = 0.91, bankroll = 10000
    # expected: total_deployed = $9100, profit = $900
    test_prices_2 = {
        "Candidate A wins": 0.55,
        "Candidate B wins": 0.36
    }
    result2 = calculate_stakes(test_prices_2, 0.91, 10000.0)
    print("Test 3:")
    print("  Stakes:", result2["stakes"])
    print("  Total deployed:", result2["total_deployed"])
    print("  Expected profit:", result2["expected_profit"])
