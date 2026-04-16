"""
Edge calculator. Compares model win probabilities
to Polymarket market prices and identifies
actionable positions.

The simulation is done upstream; this module
only receives {player: probability} dicts.

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

from config import MIN_EDGE, MIN_MARKET_PRICE, MIN_STAKE, KELLY_FRACTION, BANKROLL


def find_edges(model_probs: dict,
               market_prices: dict,
               min_edge: float = None,
               min_market_price: float = None) -> list:
    """Compares model probabilities to market prices for each player.

    Filters out players below min_market_price to avoid noise from
    near-eliminated players. Returns list of edge dicts sorted by
    abs(edge) descending.
    """
    if min_edge is None:
        min_edge = MIN_EDGE
    if min_market_price is None:
        min_market_price = MIN_MARKET_PRICE

    edges = []
    for player in model_probs:
        if player not in market_prices:
            continue

        market = market_prices[player]

        # Skip near-zero market prices — too noisy
        if market < min_market_price:
            continue

        model = model_probs[player]
        edge  = round(model - market, 4)

        if edge >= min_edge:
            action = "YES"
        elif edge <= -min_edge:
            action = "NO"
        else:
            action = "neutral"

        edges.append({
            "player": player,
            "model":  model,
            "market": market,
            "edge":   edge,
            "action": action,
        })

    return sorted(edges, key=lambda x: abs(x["edge"]), reverse=True)


def calculate_half_kelly(edge: float,
                         market_price: float,
                         bankroll: float) -> float:
    """Returns half-Kelly stake in dollars.

    For BET YES (edge > 0): buying YES at market_price.
    For BET NO  (edge < 0): buying NO at (1 - market_price).
    Enforces MIN_STAKE floor. Returns 0 if Kelly is negative.
    """
    if edge >= 0:
        # BET YES
        b = (1 - market_price) / market_price
        p = market_price + edge   # model probability
        q = 1 - p
        if b <= 0:
            return 0.0
        full_kelly = (b * p - q) / b
    else:
        # BET NO
        no_price = 1 - market_price
        b = market_price / no_price
        p = 1 - (market_price + edge)  # model NO probability
        q = 1 - p
        if b <= 0:
            return 0.0
        full_kelly = (b * p - q) / b

    if full_kelly <= 0:
        return 0.0

    stake = full_kelly * KELLY_FRACTION * bankroll
    return max(round(stake, 2), MIN_STAKE if stake > 0 else 0.0)


def size_positions(edges: list, bankroll: float = None) -> list:
    """Adds half-Kelly stake sizing to each edge dict in place.

    Returns the same list with a 'stake' key added to every entry.
    Neutral positions get stake=0.
    """
    if bankroll is None:
        bankroll = BANKROLL

    for e in edges:
        if e["action"] == "neutral":
            e["stake"] = 0.0
        else:
            e["stake"] = calculate_half_kelly(
                e["edge"], e["market"], bankroll
            )

    return edges
