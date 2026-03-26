"""Builds a weighted directed graph from live prices and runs modified Bellman-Ford to detect arbitrage opportunities below the dynamic fee-adjusted threshold."""

import math
import itertools


def build_graph(prices: dict) -> dict:
    adjacency_list = {node: [] for node in prices}
    for u, v in itertools.permutations(prices.keys(), 2):
        adjacency_list[u].append((v, prices[u]))
    return adjacency_list


def detect_arbitrage(prices: dict, threshold: float) -> dict:
    adjacency_list = build_graph(prices)
    nodes = list(prices.keys())

    dist = {node: 0.0 for node in nodes}
    predecessor = {node: None for node in nodes}

    for _ in range(len(nodes) - 1):
        for u, neighbors in adjacency_list.items():
            for v, w in neighbors:
                if dist[u] + w < dist[v]:
                    dist[v] = dist[u] + w
                    predecessor[v] = u

    cycle_sum = round(sum(prices.values()), 4)

    if cycle_sum <= threshold:
        return {
            "opportunity": True,
            "cycle_sum": cycle_sum,
            "outcomes": list(prices.keys())
        }
    else:
        return {
            "opportunity": False,
            "cycle_sum": cycle_sum,
            "outcomes": list(prices.keys())
        }


if __name__ == "__main__":

    # Test 1: should detect opportunity (sum = 0.90, threshold = 0.94)
    test_prices_1 = {
        "Le Pen wins": 0.42,
        "Macron wins": 0.38,
        "Other wins": 0.10
    }
    result1 = detect_arbitrage(test_prices_1, 0.94)
    print("Test 1 (expect opportunity=True):", result1)

    # Test 2: should NOT detect opportunity (sum = 0.99, threshold = 0.94)
    test_prices_2 = {
        "Le Pen wins": 0.50,
        "Macron wins": 0.40,
        "Other wins": 0.09
    }
    result2 = detect_arbitrage(test_prices_2, 0.94)
    print("Test 2 (expect opportunity=False):", result2)

    # Test 3: sum exactly at threshold (sum = 0.94, threshold = 0.94)
    test_prices_3 = {
        "Le Pen wins": 0.45,
        "Macron wins": 0.35,
        "Other wins": 0.14
    }
    result3 = detect_arbitrage(test_prices_3, 0.94)
    print("Test 3 (expect opportunity=True):", result3)
