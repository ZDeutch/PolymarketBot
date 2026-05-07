"""
Backtest metrics: log-loss, Brier score, calibration buckets, P&L vs market.
"""

import math
from collections import defaultdict


# ─── Per-case metrics ─────────────────────────────────────────────────────────

def log_loss(model_probs: dict, actual_winners: list, eps: float = 1e-6) -> float:
    """Negative log-likelihood of the actual winner(s) under the model.

    For joint winners, averages the log-prob of each winner.
    Lower is better. Random guess in n-player field ≈ ln(n).
    """
    if not actual_winners:
        return float("nan")
    nlls = []
    for w in actual_winners:
        p = model_probs.get(w, eps)
        p = max(eps, min(1.0 - eps, p))
        nlls.append(-math.log(p))
    return sum(nlls) / len(nlls)


def brier_score(model_probs: dict, actual_winners: list) -> float:
    """Multi-class Brier score: Σ (p_i − y_i)².

    y_i = 1 / |winners| if i is a winner, else 0. Lower is better.
    """
    target = {p: (1.0 / len(actual_winners)) if p in actual_winners else 0.0
              for p in model_probs}
    return sum((p - target.get(name, 0.0)) ** 2 for name, p in model_probs.items())


def market_log_loss(market_probs: dict, actual_winners: list,
                    eps: float = 1e-6) -> float | None:
    """Log-loss of market prices, for benchmarking against the model.

    Returns None if market_probs is empty/None.
    """
    if not market_probs:
        return None
    return log_loss(market_probs, actual_winners, eps)


# ─── Calibration ──────────────────────────────────────────────────────────────

def calibration_buckets(predictions: list, n_buckets: int = 10) -> list[dict]:
    """Reliability diagram buckets.

    `predictions` is a list of (model_prob, won_bool) pairs across all
    cases. Returns N buckets sorted by predicted probability with mean
    predicted vs realized hit rate.
    """
    buckets = [[] for _ in range(n_buckets)]
    for p, won in predictions:
        idx = min(int(p * n_buckets), n_buckets - 1)
        buckets[idx].append((p, 1.0 if won else 0.0))

    out = []
    for i, bucket in enumerate(buckets):
        if not bucket:
            out.append({
                "bucket":     i,
                "low":        i / n_buckets,
                "high":       (i + 1) / n_buckets,
                "n":          0,
                "mean_pred":  None,
                "hit_rate":   None,
            })
            continue
        ps = [p for p, _ in bucket]
        ws = [w for _, w in bucket]
        out.append({
            "bucket":     i,
            "low":        i / n_buckets,
            "high":       (i + 1) / n_buckets,
            "n":          len(bucket),
            "mean_pred":  sum(ps) / len(ps),
            "hit_rate":   sum(ws) / len(ws),
        })
    return out


# ─── P&L vs market ────────────────────────────────────────────────────────────

def quarter_kelly_pnl(model_probs: dict, market_probs: dict,
                      actual_winners: list, bankroll: float = 10000.0,
                      kelly_frac: float = 0.25,
                      min_edge: float = 0.05) -> dict:
    """Quarter-Kelly realized P&L on YES/NO contracts at market prices.

    For each player:
      edge = model_p − market_p
      If |edge| >= min_edge: bet on the side with positive edge.
        bet_frac = kelly_frac · edge / (price · (1 − price))
        size     = bankroll · max(0, min(0.10, bet_frac))   # capped 10%/leg
      P&L = (1 − price) on win, (−price) on loss for YES
            (price)    on win, (price − 1) on loss for NO   (mirrored)

    Returns {"trades": int, "stake": float, "pnl": float, "by_player": {...}}.
    """
    summary = {"trades": 0, "stake": 0.0, "pnl": 0.0, "by_player": {}}
    if not market_probs:
        return summary

    won_set = set(actual_winners)
    for player, model_p in model_probs.items():
        market_p = market_probs.get(player)
        if market_p is None:
            continue
        edge = model_p - market_p
        if abs(edge) < min_edge:
            continue
        side = "YES" if edge > 0 else "NO"
        price = market_p if side == "YES" else (1.0 - market_p)
        if price <= 0 or price >= 1:
            continue
        bet_frac = kelly_frac * abs(edge) / (price * (1 - price))
        size     = bankroll * max(0.0, min(0.10, bet_frac))
        won      = player in won_set
        if side == "YES":
            payoff = (1.0 - price) if won else (-price)
        else:
            payoff = price if not won else (-(1.0 - price))
        pnl_i = size * (payoff / price)   # convert price-units to dollar P&L
        summary["trades"]               += 1
        summary["stake"]                += size
        summary["pnl"]                  += pnl_i
        summary["by_player"][player]    = {
            "side":     side,
            "edge":     round(edge, 3),
            "price":    round(price, 3),
            "size":     round(size, 2),
            "pnl":      round(pnl_i, 2),
            "won":      won,
        }
    return summary
