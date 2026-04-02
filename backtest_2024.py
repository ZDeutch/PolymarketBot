"""Backtests the elo_model.py methodology against the 2024 FIDE Candidates
tournament. Uses identical model logic — Pav draw rate, +35 white bonus,
σ=50 performance variance — but feeds in April 2024 ratings and the actual
game results after round 2, then checks whether the model would have
identified the eventual winner (Gukesh Dommaraju)."""

import random
import math
from datetime import datetime

# ─── Constants ────────────────────────────────────────────────────────────────

SIMULATIONS          = 50000
WHITE_BONUS          = 35
DRAW_LOGIT_INTERCEPT = -0.0198
DRAW_LOGIT_RDIFF_COEF =  0.00687
DRAW_LOGIT_MEAN_COEF  = -0.000421

# ─── 2024 Data ────────────────────────────────────────────────────────────────

# April 2024 FIDE classical ratings
PLAYERS_2024 = {
    "Fabiano Caruana":    2803,
    "Hikaru Nakamura":    2789,
    "Alireza Firouzja":   2760,
    "Ian Nepomniachtchi": 2758,
    "Praggnanandhaa R":   2747,
    "Gukesh Dommaraju":   2743,
    "Vidit Gujrathi":     2727,
    "Nijat Abasov":       2632,
}

# Actual results after round 2 only
# (same information we have for 2026 right now)
COMPLETED_GAMES_2024 = [
    # Round 1
    ("Fabiano Caruana",    "Hikaru Nakamura",    0.5, 0.5),
    ("Nijat Abasov",       "Ian Nepomniachtchi", 0.5, 0.5),
    ("Alireza Firouzja",   "Praggnanandhaa R",   0.5, 0.5),
    ("Gukesh Dommaraju",   "Vidit Gujrathi",     0.5, 0.5),
    # Round 2
    ("Hikaru Nakamura",    "Vidit Gujrathi",     0.0, 1.0),
    ("Praggnanandhaa R",   "Gukesh Dommaraju",   0.0, 1.0),
    ("Ian Nepomniachtchi", "Alireza Firouzja",   1.0, 0.0),
    ("Fabiano Caruana",    "Nijat Abasov",       1.0, 0.0),
]

# Remaining rounds 3-14 pairings: (white, black)
REMAINING_GAMES_2024 = [
    # Round 3
    ("Nijat Abasov",       "Hikaru Nakamura"),
    ("Alireza Firouzja",   "Fabiano Caruana"),
    ("Gukesh Dommaraju",   "Ian Nepomniachtchi"),
    ("Vidit Gujrathi",     "Praggnanandhaa R"),
    # Round 4
    ("Hikaru Nakamura",    "Praggnanandhaa R"),
    ("Ian Nepomniachtchi", "Vidit Gujrathi"),
    ("Fabiano Caruana",    "Gukesh Dommaraju"),
    ("Nijat Abasov",       "Alireza Firouzja"),
    # Round 5
    ("Alireza Firouzja",   "Hikaru Nakamura"),
    ("Gukesh Dommaraju",   "Nijat Abasov"),
    ("Vidit Gujrathi",     "Fabiano Caruana"),
    ("Praggnanandhaa R",   "Ian Nepomniachtchi"),
    # Round 6
    ("Gukesh Dommaraju",   "Hikaru Nakamura"),
    ("Vidit Gujrathi",     "Alireza Firouzja"),
    ("Praggnanandhaa R",   "Nijat Abasov"),
    ("Ian Nepomniachtchi", "Fabiano Caruana"),
    # Round 7
    ("Hikaru Nakamura",    "Ian Nepomniachtchi"),
    ("Fabiano Caruana",    "Praggnanandhaa R"),
    ("Nijat Abasov",       "Vidit Gujrathi"),
    ("Alireza Firouzja",   "Gukesh Dommaraju"),
    # Round 8
    ("Hikaru Nakamura",    "Fabiano Caruana"),
    ("Ian Nepomniachtchi", "Nijat Abasov"),
    ("Praggnanandhaa R",   "Alireza Firouzja"),
    ("Vidit Gujrathi",     "Gukesh Dommaraju"),
    # Round 9
    ("Vidit Gujrathi",     "Hikaru Nakamura"),
    ("Gukesh Dommaraju",   "Praggnanandhaa R"),
    ("Alireza Firouzja",   "Ian Nepomniachtchi"),
    ("Nijat Abasov",       "Fabiano Caruana"),
    # Round 10
    ("Hikaru Nakamura",    "Nijat Abasov"),
    ("Fabiano Caruana",    "Alireza Firouzja"),
    ("Ian Nepomniachtchi", "Gukesh Dommaraju"),
    ("Praggnanandhaa R",   "Vidit Gujrathi"),
    # Round 11
    ("Praggnanandhaa R",   "Hikaru Nakamura"),
    ("Vidit Gujrathi",     "Ian Nepomniachtchi"),
    ("Gukesh Dommaraju",   "Fabiano Caruana"),
    ("Alireza Firouzja",   "Nijat Abasov"),
    # Round 12
    ("Hikaru Nakamura",    "Alireza Firouzja"),
    ("Nijat Abasov",       "Gukesh Dommaraju"),
    ("Fabiano Caruana",    "Vidit Gujrathi"),
    ("Ian Nepomniachtchi", "Praggnanandhaa R"),
    # Round 13
    ("Ian Nepomniachtchi", "Hikaru Nakamura"),
    ("Praggnanandhaa R",   "Fabiano Caruana"),
    ("Vidit Gujrathi",     "Nijat Abasov"),
    ("Gukesh Dommaraju",   "Alireza Firouzja"),
    # Round 14
    ("Hikaru Nakamura",    "Gukesh Dommaraju"),
    ("Fabiano Caruana",    "Ian Nepomniachtchi"),
    ("Alireza Firouzja",   "Vidit Gujrathi"),
    ("Nijat Abasov",       "Praggnanandhaa R"),
]


# ─── Core functions (copied exactly from elo_model.py) ────────────────────────

def elo_win_prob(rating_a: float, rating_b: float) -> float:
    """Returns P(A wins) in a single game against B (excluding draws)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def pav_draw_rate(rating_a: float, rating_b: float) -> float:
    """Returns P(draw) for a single game using the Pav logistic model
    (gilgamath.com, fitted on 1.18 million rated games).
    Inputs are the effective ratings (white already boosted by WHITE_BONUS)."""
    delta = abs(rating_a - rating_b)
    mean  = (rating_a + rating_b) / 2.0
    logit_decisive = (DRAW_LOGIT_INTERCEPT
                      + DRAW_LOGIT_RDIFF_COEF * delta
                      + DRAW_LOGIT_MEAN_COEF  * mean)
    p_decisive = 1.0 / (1.0 + math.exp(-logit_decisive))
    return round(1.0 - p_decisive, 4)


def simulate_game(player_a: str, player_b: str, ratings: dict) -> tuple:
    """Simulates one game. Returns (score_a, score_b).
    player_a is white; WHITE_BONUS is added to white's effective rating.
    Draw rate is computed per-game from the Pav logistic model."""
    white_rating = ratings[player_a] + WHITE_BONUS
    black_rating = ratings[player_b]

    p_a_wins  = elo_win_prob(white_rating, black_rating)
    draw_rate = pav_draw_rate(white_rating, black_rating)

    p_a_decisive = p_a_wins * (1 - draw_rate)
    p_b_decisive = (1 - p_a_wins) * (1 - draw_rate)
    p_draw       = draw_rate

    r = random.random()
    if r < p_a_decisive:
        return (1.0, 0.0)
    elif r < p_a_decisive + p_draw:
        return (0.5, 0.5)
    else:
        return (0.0, 1.0)


# ─── Simulation ───────────────────────────────────────────────────────────────

def run_simulation_2024() -> dict:
    """Monte Carlo simulation of 2024 Candidates from round 3 onward.
    Identical logic to run_simulation() in elo_model.py."""
    players    = list(PLAYERS_2024.keys())
    win_counts = {p: 0 for p in players}

    for _ in range(SIMULATIONS):

        # Sample performance ratings for this iteration (σ=50)
        perf_ratings = {p: random.gauss(PLAYERS_2024[p], 50) for p in players}

        # Start with actual scores from completed games
        scores = {p: 0.0 for p in players}
        for white, black, ws, bs in COMPLETED_GAMES_2024:
            scores[white] += ws
            scores[black] += bs

        # Simulate remaining games using this iteration's performance ratings
        for white, black in REMAINING_GAMES_2024:
            ws, bs = simulate_game(white, black, perf_ratings)
            scores[white] += ws
            scores[black] += bs

        # Find winner (highest score), random tiebreak
        max_score = max(scores.values())
        winners   = [p for p, s in scores.items() if s == max_score]
        win_counts[random.choice(winners)] += 1

    return {p: round(win_counts[p] / SIMULATIONS, 4) for p in players}


# ─── Entry Point ──────────────────────────────────────────────────────────────

def run():
    print("FIDE Candidates 2024 — Backtest (after round 2)")
    print("Using identical model to our 2026 simulator")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print()

    # ── Actual standings after round 2 ────────────────────────────────────────
    scores_r2 = {p: 0.0 for p in PLAYERS_2024}
    for white, black, ws, bs in COMPLETED_GAMES_2024:
        scores_r2[white] += ws
        scores_r2[black] += bs

    print("── STANDINGS AFTER ROUND 2 ──")
    for player, score in sorted(scores_r2.items(), key=lambda x: x[1], reverse=True):
        flag = " ★ (actual winner)" if player == "Gukesh Dommaraju" else ""
        print(f"  {player:<25} {score:.1f}{flag}")
    print()

    # ── Model win probabilities ────────────────────────────────────────────────
    print(f"Running {SIMULATIONS:,} simulations...")
    model_probs = run_simulation_2024()
    print()

    ranked = sorted(model_probs.items(), key=lambda x: x[1], reverse=True)

    print("── MODEL WIN PROBABILITIES ──")
    print(f"  {'Rank':<5} {'Player':<25} {'Elo':>5}  {'Score/R2':>8}  {'Model%':>7}")
    print("  " + "─" * 55)
    for rank, (player, prob) in enumerate(ranked, start=1):
        elo   = PLAYERS_2024[player]
        score = scores_r2[player]
        flag  = " ★" if player == "Gukesh Dommaraju" else ""
        print(f"  {rank:<5} {player:<25} {elo:>5}  {score:>8.1f}  {prob*100:>6.1f}%{flag}")
    print()

    # ── Verdict ───────────────────────────────────────────────────────────────
    model_winner   = ranked[0][0]
    gukesh_prob    = model_probs["Gukesh Dommaraju"]
    gukesh_rank    = next(i + 1 for i, (p, _) in enumerate(ranked) if p == "Gukesh Dommaraju")

    print("── VERDICT ──")
    print(f"  Model predicted winner:       {model_winner} ({model_probs[model_winner]*100:.1f}%)")
    print(f"  Gukesh model probability:     {gukesh_prob*100:.1f}%")
    print(f"  Actual winner:                Gukesh Dommaraju ★")
    print(f"  Model rank for actual winner: {gukesh_rank} of 8")

    # Plain-English assessment
    print()
    if gukesh_rank == 1:
        print("  ✓ Model correctly identified the winner as favourite.")
    elif gukesh_rank <= 3:
        print(f"  ~ Model had Gukesh top-{gukesh_rank} — reasonable but not top pick.")
    else:
        print(f"  ✗ Model ranked Gukesh {gukesh_rank}th — underestimated the actual winner.")


if __name__ == "__main__":
    run()
