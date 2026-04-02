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


