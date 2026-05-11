"""
Monte Carlo simulator for round-robin chess
tournaments.

Simulation model:
  - Per-player draw rates from tpr_data.json,
    scaled by Kryukov rating-diff factor;
    falls back to Kryukov when data unavailable
  - Kryukov draw-rate baseline (exponential
    mean-rating term captures 2700+ draw surge)
  - Must-win draw adjustment: players trailing
    the leader by ≥ MUST_WIN_SCORE_GAP with
    ≤ MUST_WIN_GAMES_LEFT remaining have their
    draw probability multiplied by
    MUST_WIN_DRAW_FACTOR (0.85) per such player
  - +35 Elo white piece bonus
  - TPR-blended ratings with N(rating, σ) where σ is per-player from
    tpr_data.json (default 50 if not found)
    performance variance per iteration
  - Random tiebreak (simplified)
  - 50,000 iterations default

Key functions:

  elo_win_prob(rating_a: float,
               rating_b: float) -> float
    Standard Elo win probability formula.
    P(A beats B) = 1 / (1 + 10^((B-A)/400))

  kryukov_draw_rate(rating_a: float,
                    rating_b: float) -> float
    Kryukov exponential model for draw probability.
    draw% = -|Δ|/32.49 + exp((μ−2254.7)/208.49) + 23.87
    Outperforms Pav logistic at 2700+ average rating.

  simulate_game(white: str, black: str,
                ratings: dict,
                scores: dict | None,
                games_left: dict | None) -> tuple
    Simulates one game. Returns (white_score,
    black_score). Applies white piece bonus.
    Uses per-player + Kryukov draw model with
    optional must-win draw reduction.

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
_TPR_DATA_PATH   = None   # explicit override for backtests


TPR_STALENESS_DAYS = 14   # warn if tpr_data.json is older than this


def set_tpr_data_path(path: str | None) -> None:
    """Override the TPR data source. Pass None to reset to default.

    Forces a reload on the next call to any function that needs TPR data.
    Used by the backtest runner to swap in per-case (asof-rebuilt) datasets.
    """
    global _TPR_DATA, _TPR_DATA_LOADED, _TPR_DATA_PATH
    _TPR_DATA_PATH   = path
    _TPR_DATA        = {}
    _TPR_DATA_LOADED = False


def _load_tpr_data() -> None:
    """Loads tpr_data.json once on first call; no-ops on subsequent calls.

    Emits a staleness warning if the file's `generated_at` timestamp is
    older than TPR_STALENESS_DAYS. Does not block — re-run
    `python tpr_builder.py` to refresh.
    """
    global _TPR_DATA, _TPR_DATA_LOADED
    if _TPR_DATA_LOADED:
        return
    if _TPR_DATA_PATH:
        path = _TPR_DATA_PATH
    else:
        path = os.path.join(os.path.dirname(__file__), "..", "tpr_data.json")
    try:
        with open(path) as f:
            raw = json.load(f)
        _TPR_DATA = raw.get("players", {})
        _TPR_DATA_LOADED = True
        print(f"  Loaded TPR data: {len(_TPR_DATA)} players")

        # Staleness check
        gen_at = raw.get("generated_at")
        if gen_at:
            try:
                from datetime import datetime
                ts = datetime.fromisoformat(gen_at)
                age_days = (datetime.now() - ts).days
                if age_days > TPR_STALENESS_DAYS:
                    print(
                        f"  WARNING: tpr_data.json is {age_days} days old "
                        f"(>{TPR_STALENESS_DAYS}). Run `python tpr_builder.py` to refresh."
                    )
                else:
                    print(f"  TPR data age: {age_days} day(s)")
            except Exception:
                pass
    except Exception as e:
        print(f"  WARNING: could not load tpr_data.json: {e}")
        _TPR_DATA_LOADED = True   # don't retry

# ─── TPR blend ────────────────────────────────────────────────────────────────

def get_player_sigma(player: str) -> float:
    """Returns per-player performance σ (Elo) from tpr_data.json.

    Falls back to DEFAULT_SIGMA when no volatility estimate is available.
    """
    _load_tpr_data()
    data = _TPR_DATA.get(player)
    if not data:
        return DEFAULT_SIGMA
    vol = data.get("volatility")
    if vol is None:
        return DEFAULT_SIGMA
    return float(vol)


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

# Per-iteration performance noise. DEFAULT_SIGMA is used as a fallback
# when a player has no per-player volatility estimate in tpr_data.json.
DEFAULT_SIGMA = 50.0

# Must-win draw adjustment (Change 6)
MUST_WIN_DRAW_FACTOR = 0.85   # 15 % relative draw-rate reduction per must-win player
MUST_WIN_SCORE_GAP   = 2      # trail leader by ≥ this many points …
MUST_WIN_GAMES_LEFT  = 4      # … with ≤ this many games remaining (incl. current)


# ─── Core probability functions ───────────────────────────────────────────────

def elo_win_prob(rating_a: float, rating_b: float) -> float:
    """Returns P(A wins) in a single game against B (excluding draws).

    P(A beats B) = 1 / (1 + 10^((B-A)/400))
    """
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def kryukov_draw_rate(rating_a: float, rating_b: float) -> float:
    """Returns P(draw) using the Kryukov exponential model.

    draw% = -|Δ|/32.49 + exp((μ − 2254.7)/208.49) + 23.87

    The exponential mean-rating term captures the accelerating draw rate
    at 2700+ average Elo that Pav's linear term systematically underestimates.
    Clamped to [0.0, 0.85].
    """
    delta    = abs(rating_a - rating_b)
    mu       = (rating_a + rating_b) / 2.0
    draw_pct = -delta / 32.49 + math.exp((mu - 2254.7) / 208.49) + 23.87
    return round(max(0.0, min(0.85, draw_pct / 100.0)), 4)


def simulate_game(white: str, black: str, ratings: dict,
                  scores: dict | None = None,
                  games_left: dict | None = None) -> tuple:
    """Simulates one game. white gets +WHITE_BONUS to effective rating.

    Draw rate (Changes 2 + 3):
      If both players have empirical draw rates in tpr_data.json:
        base = (dr_white + dr_black) / 2
        scaled by Kryukov's rating-diff factor relative to equal-rating baseline
        → preserves per-player draw tendencies while penalising mismatches
      Otherwise: Kryukov formula directly (Change 3 fallback).

    Must-win adjustment (Change 6):
      After the base draw rate is computed, for each player who trails
      the current leader by ≥ MUST_WIN_SCORE_GAP points and has
      ≤ MUST_WIN_GAMES_LEFT games remaining (including this one),
      draw_rate is multiplied by MUST_WIN_DRAW_FACTOR (0.85).
      Applied independently per qualifying player, so two must-win
      players facing each other compound the reduction (0.85²≈0.72).
      Requires scores and games_left to be passed; no-ops otherwise.

    Returns (white_score, black_score).
    """
    white_eff = ratings[white] + WHITE_BONUS
    black_eff = ratings[black]

    p_white_wins = elo_win_prob(white_eff, black_eff)

    # ── Draw rate ─────────────────────────────────────────────────────────────
    dr_white = _TPR_DATA.get(white, {}).get("draw_rate")
    dr_black = _TPR_DATA.get(black, {}).get("draw_rate")

    if dr_white is not None and dr_black is not None:
        # Per-player empirical average, scaled by Kryukov rating-diff penalty
        avg_eff      = (white_eff + black_eff) / 2.0
        kryukov_ref  = kryukov_draw_rate(avg_eff, avg_eff)  # zero diff, same mean
        kryukov_full = kryukov_draw_rate(white_eff, black_eff)
        player_avg   = (dr_white + dr_black) / 2.0
        draw_rate    = (player_avg * kryukov_full / kryukov_ref
                        if kryukov_ref > 0 else player_avg)
    else:
        # Fallback: Kryukov formula alone
        draw_rate = kryukov_draw_rate(white_eff, black_eff)

    # ── Must-win draw adjustment ───────────────────────────────────────────────
    if scores is not None and games_left is not None:
        leader = max(scores.values())
        for player in (white, black):
            gap  = leader - scores[player]
            left = games_left.get(player, 0)
            if gap >= MUST_WIN_SCORE_GAP and left <= MUST_WIN_GAMES_LEFT:
                draw_rate *= MUST_WIN_DRAW_FACTOR

    draw_rate = max(0.0, min(0.85, draw_rate))

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
      1. Samples per-player performance ratings from N(rating, σ) where σ is per-player
      2. Seeds scores from actual completed results
      3. Simulates all remaining games (with must-win draw adjustment)
      4. Awards the win to the highest scorer (random tiebreak)

    Returns {player: win_probability}.
    """
    players    = list(ratings.keys())
    win_counts = {p: 0 for p in players}

    # Precompute starting games_left from the fixed remaining schedule.
    # Copied cheaply each iteration and decremented as games are played,
    # so simulate_game always sees the correct count (incl. current game).
    initial_games_left: dict[str, int] = {p: 0 for p in players}
    for w, b in remaining:
        initial_games_left[w] = initial_games_left.get(w, 0) + 1
        initial_games_left[b] = initial_games_left.get(b, 0) + 1

    # Precompute per-player σ once (outside the inner loop)
    sigmas = {p: get_player_sigma(p) for p in players}

    for _ in range(n):
        # Sample performance ratings: TPR blend ± per-player σ noise
        perf = {
            p: random.gauss(get_adjusted_rating(p, ratings[p]), sigmas[p])
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

        # games_left is mutable per iteration; copy once from precomputed base
        games_left = dict(initial_games_left)

        # Simulate remaining games
        for white, black in remaining:
            if white not in perf or black not in perf:
                continue
            ws, bs = simulate_game(white, black, perf,
                                   scores=scores, games_left=games_left)
            scores[white] += ws
            scores[black] += bs
            games_left[white] -= 1
            games_left[black] -= 1

        # Find winner — random tiebreak
        max_score = max(scores.values())
        winners   = [p for p, s in scores.items() if s == max_score]
        win_counts[random.choice(winners)] += 1

    return {p: round(win_counts[p] / n, 4) for p in players}
