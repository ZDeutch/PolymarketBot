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
