"""
Monte Carlo simulator for round-robin chess
tournaments.

Simulation model:
  - Pav logistic draw rate (rating diff + avg rating)
  - +35 Elo white piece bonus
  - N(rating, σ=50) performance variance per iteration
  - Random tiebreak (simplified)
  - 50,000 iterations default

Key functions:

  elo_win_prob(rating_a: float,
               rating_b: float) -> float
    Standard Elo win probability formula.
    P(A beats B) = 1 / (1 + 10^((B-A)/400))

  pav_draw_rate(rating_a: float,
                rating_b: float) -> float
    Pav logistic model for draw probability.
    Uses rating difference and average rating.
    Fitted on 1.18M rated games.

  simulate_game(white: str, black: str,
                ratings: dict) -> tuple
    Simulates one game. Returns (white_score,
    black_score). Applies white piece bonus.

  simulate_tournament(ratings: dict,
                      completed: dict,
                      remaining: list,
                      n: int) -> dict
    Runs n Monte Carlo simulations of remaining
    tournament. Returns {player: win_probability}.
"""

import json
import math
import os
import random

# ─── TPR data (lazy-loaded from tpr_data.json) ────────────────────────────────

_TPR_DATA        = {}
_TPR_DATA_LOADED = False


def _load_tpr_data() -> None:
    """Loads tpr_data.json once on first call; no-ops on subsequent calls."""
    global _TPR_DATA, _TPR_DATA_LOADED
    if _TPR_DATA_LOADED:
        return
    path = os.path.join(os.path.dirname(__file__), "..", "tpr_data.json")
    try:
        with open(path) as f:
            raw = json.load(f)
        _TPR_DATA = raw.get("players", {})
        _TPR_DATA_LOADED = True
        print(f"  Loaded TPR data: {len(_TPR_DATA)} players")
    except Exception as e:
        print(f"  WARNING: could not load tpr_data.json: {e}")
        _TPR_DATA_LOADED = True   # don't retry

# ─── TPR blend ────────────────────────────────────────────────────────────────

def get_adjusted_rating(player: str, fide_elo: float) -> float:
    """Blends FIDE Elo with elite TPR for a more accurate strength estimate.

    Formula (full data):
      adjusted = 0.45 * fide_elo + 0.55 * tpr

    Reliability scaling: if fewer than 15 elite games are available, the
    TPR weight is reduced proportionally so sparse data doesn't dominate:
      weight_tpr  = 0.55 * min(games, 15) / 15
      weight_fide = 1.0 - weight_tpr

    Velocity adjustment: captures players whose recent form significantly
    exceeds or trails their accumulated FIDE rating:
      velocity = (tpr - fide_elo) * 0.15

    Clamp: the total adjustment (blended + velocity − fide_elo) is capped
    at ±MAX_ADJUSTMENT Elo so small-sample noise cannot produce extreme shifts.

    Falls back to raw FIDE Elo if no TPR data is available.
    """
    MAX_ADJUSTMENT = 50   # Elo points — hard ceiling on any single adjustment

    _load_tpr_data()

    player_data = _TPR_DATA.get(player)
    if not player_data:
        return fide_elo

    tpr   = player_data.get("tpr")
    games = player_data.get("elite_games", 0)

    if tpr is None or games == 0:
        return fide_elo

    # Scale TPR weight by data reliability (full weight at 15+ games)
    weight_tpr  = 0.55 * min(games, 15) / 15
    weight_fide = 1.0 - weight_tpr

    blended  = weight_fide * fide_elo + weight_tpr * tpr
    velocity = (tpr - fide_elo) * 0.15

    raw_delta     = (blended + velocity) - fide_elo
    clamped_delta = max(-MAX_ADJUSTMENT, min(MAX_ADJUSTMENT, raw_delta))
    adjusted      = fide_elo + clamped_delta

    return round(adjusted, 1)


# ─── Constants ────────────────────────────────────────────────────────────────

WHITE_BONUS = 35  # Elo points added to white's effective rating (Sonas, 266k games)

# Pav logistic draw-rate model (gilgamath.com, fitted on 1.18 M rated games)
DRAW_LOGIT_INTERCEPT  = -0.0198
DRAW_LOGIT_RDIFF_COEF =  0.00687
DRAW_LOGIT_MEAN_COEF  = -0.000421


# ─── Core probability functions ───────────────────────────────────────────────

def elo_win_prob(rating_a: float, rating_b: float) -> float:
    """Returns P(A wins) in a single game against B (excluding draws).

    P(A beats B) = 1 / (1 + 10^((B-A)/400))
    """
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def pav_draw_rate(rating_a: float, rating_b: float) -> float:
    """Returns P(draw) using the Pav logistic model (gilgamath.com).

    Fitted on 1.18M rated games. Uses rating difference and average rating.
    """
    delta = abs(rating_a - rating_b)
    mean  = (rating_a + rating_b) / 2.0
    logit_decisive = (DRAW_LOGIT_INTERCEPT
                      + DRAW_LOGIT_RDIFF_COEF * delta
                      + DRAW_LOGIT_MEAN_COEF  * mean)
    p_decisive = 1.0 / (1.0 + math.exp(-logit_decisive))
    return round(1.0 - p_decisive, 4)


def simulate_game(white: str, black: str, ratings: dict) -> tuple:
    """Simulates one game. white gets +WHITE_BONUS to effective rating.

    Returns (white_score, black_score).
    """
    white_rating = ratings[white] + WHITE_BONUS
    black_rating = ratings[black]

    p_white_wins = elo_win_prob(white_rating, black_rating)
    draw_rate    = pav_draw_rate(white_rating, black_rating)

    p_white_decisive = p_white_wins * (1 - draw_rate)
    p_draw           = draw_rate

    r = random.random()
    if r < p_white_decisive:
        return (1.0, 0.0)
    elif r < p_white_decisive + p_draw:
        return (0.5, 0.5)
    else:
        return (0.0, 1.0)


# ─── Simulation ───────────────────────────────────────────────────────────────

def simulate_tournament(ratings: dict,
                        completed: dict,
                        remaining: list,
                        n: int = 50000) -> dict:
    """Runs n Monte Carlo simulations of the remaining tournament.

    Each iteration:
      1. Samples per-player performance ratings from N(rating, σ=50)
      2. Seeds scores from actual completed results
      3. Simulates all remaining games
      4. Awards the win to the highest scorer (random tiebreak)

    Returns {player: win_probability}.
    """
    players    = list(ratings.keys())
    win_counts = {p: 0 for p in players}

    for _ in range(n):
        # Sample performance ratings: apply TPR blend first, then σ=50 noise
        perf = {
            p: random.gauss(get_adjusted_rating(p, ratings[p]), 50)
            for p in players
        }

        # Seed scores from actual completed results
        scores = {p: 0.0 for p in players}
        for round_games in completed.values():
            for white, black, ws, bs in round_games:
                if white in scores:
                    scores[white] += ws
                if black in scores:
                    scores[black] += bs

        # Simulate remaining games
        for white, black in remaining:
            if white not in perf or black not in perf:
                continue
            ws, bs = simulate_game(white, black, perf)
            scores[white] += ws
            scores[black] += bs

        # Find winner — random tiebreak
        max_score = max(scores.values())
        winners   = [p for p, s in scores.items() if s == max_score]
        win_counts[random.choice(winners)] += 1

    return {p: round(win_counts[p] / n, 4) for p in players}
