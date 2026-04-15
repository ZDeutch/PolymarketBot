"""
Monte Carlo simulator for stroke play golf
tournaments.

Simulation model:
  - Per-round score sampled from N(μ, σ)
  - μ = player scoring average adjusted for
    course difficulty
  - σ = player scoring standard deviation
    (typically 2.5-3.5 strokes at tour level)
  - Cut applied after round 2 (top 65 + ties)
  - 50,000 iterations default

Key functions:

  simulate_round(player: str,
                 player_stats: dict,
                 course_difficulty: float) -> float
    Simulates one round score for a player.
    Samples from N(scoring_avg + course_difficulty,
                   scoring_std_dev)

  apply_cut(scores: dict,
            cut_line: float) -> dict
    Removes players who missed the cut.
    Cut line = top 65 players + ties after round 2.

  simulate_tournament(field: dict,
                      completed_scores: dict,
                      completed_rounds: int,
                      n: int) -> dict
    Runs n Monte Carlo simulations of remaining
    rounds. Initializes with actual completed
    round scores. Applies cut after round 2 if
    not already applied.
    Returns {player: win_probability}.

Note: Unlike chess/tennis, golf has no matchups.
All players compete independently against the
course. The winner is simply whoever has the
lowest total score after 4 rounds.
"""

import random
import math

# ─── Constants ────────────────────────────────────────────────────────────────

SCORING_STD_DEV    = 3.0   # stroke standard deviation per round at tour level
CUT_SIZE           = 65    # top 65 + ties make the cut
DEFAULT_SCORING_AVG = 0.0  # vs field average (for players not in OWGR)


# ─── Rating → scoring average ──────────────────────────────────────────────────

def owgr_to_scoring_avg(rank: int) -> float:
    """Maps OWGR rank to expected scoring average relative to field average.

    Linear mapping:
      rank   1  →  -2.0 strokes/round (2 below field avg)
      rank  50  →   0.0 strokes/round (at field average)
      rank 150  →  +2.0 strokes/round (2 above field avg)

    Clamped to [-3.0, +3.0].
    """
    avg = (rank - 50) * (2.0 / 100)
    return max(-3.0, min(3.0, avg))


# ─── Round simulation ──────────────────────────────────────────────────────────

def simulate_round(player: str,
                   scoring_avg: float,
                   std_dev: float = SCORING_STD_DEV) -> float:
    """Simulates one round score relative to field average.

    Samples from N(scoring_avg, std_dev). Returns a float
    (strokes relative to field average for that round).
    """
    return random.gauss(scoring_avg, std_dev)


# ─── Cut ──────────────────────────────────────────────────────────────────────

def apply_cut(cumulative_scores: dict,
              cut_size: int = CUT_SIZE) -> set:
    """Determines who makes the cut after round 2.

    Sorts players by cumulative score (lowest = best).
    Takes the top cut_size players plus any tied with
    the player in position cut_size ('+ ties' rule).

    Returns set of player names who made the cut.
    """
    if not cumulative_scores:
        return set()

    sorted_players = sorted(cumulative_scores.items(), key=lambda x: x[1])

    if len(sorted_players) <= cut_size:
        return {p for p, _ in sorted_players}

    # Score of the last player inside the cut
    cut_score = sorted_players[cut_size - 1][1]

    # Include all players at or better than the cut score (handles ties)
    return {p for p, s in sorted_players if s <= cut_score}


# ─── Tournament simulation ────────────────────────────────────────────────────

def simulate_tournament(field: dict,
                        completed_scores: dict,
                        completed_rounds: int,
                        n: int = 50000) -> dict:
    """Runs n Monte Carlo simulations of the remaining tournament.

    field:             {player_name: owgr_rank}
    completed_scores:  from golf_fetcher.get_live_scores()
                       {player_name: {round_1..4, made_cut, ...}}
    completed_rounds:  0-4, how many rounds are fully done

    Each iteration:
      1. Seed cumulative scores from actual completed round data.
      2. Determine who survives the cut (actual data if rounds >= 2,
         else simulate and apply after round 2).
      3. Simulate remaining rounds for survivors.
      4. Winner = lowest cumulative score (random tiebreak).

    Returns {player_name: win_probability}.
    """
    players    = list(field.keys())
    win_counts = {p: 0 for p in players}

    for _ in range(n):
        # ── 1. Seed from actual completed round scores ────────────────────
        cumulative = {p: 0.0 for p in players}
        for player in players:
            pdata = completed_scores.get(player, {})
            for r in range(1, completed_rounds + 1):
                r_score = pdata.get(f"round_{r}")
                if r_score is not None:
                    cumulative[player] += r_score
                # If actual score is missing (e.g. late withdrawal),
                # leave as 0 — player will appear uncompetitive.

        # ── 2. Survivors (who made the cut) ──────────────────────────────
        if completed_rounds >= 2:
            # Use actual cut results from ESPN data
            survivors = {
                p for p in players
                if completed_scores.get(p, {}).get("made_cut", True)
            }
        else:
            # Pre-cut: everyone is still in
            survivors = set(players)

        # ── 3. Simulate remaining rounds ─────────────────────────────────
        for round_num in range(completed_rounds + 1, 5):
            # Simulate this round for all survivors
            for player in survivors:
                rank    = field.get(player, 100)
                avg     = owgr_to_scoring_avg(rank)
                sim_score = simulate_round(player, avg)
                cumulative[player] += sim_score

            # Apply cut after round 2 if not yet determined from actual data
            if round_num == 2 and completed_rounds < 2:
                survivors = apply_cut(
                    {p: cumulative[p] for p in survivors}
                )

        # ── 4. Winner = lowest score among survivors ──────────────────────
        eligible = {p: cumulative[p] for p in survivors} if survivors else cumulative

        min_score = min(eligible.values())
        winners   = [p for p, s in eligible.items() if s == min_score]
        win_counts[random.choice(winners)] += 1

    return {p: round(win_counts[p] / n, 4) for p in players}
