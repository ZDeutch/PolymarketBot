"""
Sport-agnostic edge calculator. Compares model
win probabilities to Polymarket market prices
and identifies actionable positions.

Works identically for chess, tennis, and golf —
the sport-specific simulation is already done
upstream and this module only sees probabilities.

Functions:

  find_edges(model_probs: dict,
             market_prices: dict,
             min_edge: float,
             min_market_price: float) -> list
    Compares model to market for each player.
    Filters out positions below min_market_price
    (to avoid noise from sub-5% probabilities).
    Returns list of edge dicts sorted by
    abs(edge) descending:
    [
      {
        "player": str,
        "model": float,
        "market": float,
        "edge": float,
        "action": "YES" | "NO"
      }
    ]

  calculate_half_kelly(edge: float,
                       market_price: float,
                       bankroll: float) -> float
    Calculates half-Kelly stake size.
    Returns 0 if Kelly is negative.
    Enforces MIN_STAKE floor.

  size_positions(edges: list,
                 bankroll: float) -> list
    Adds stake sizing to each edge dict.
    Returns same list with "stake" key added.
"""
