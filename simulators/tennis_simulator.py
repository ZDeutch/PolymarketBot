"""
Monte Carlo simulator for knockout bracket
tennis tournaments.

Simulation model:
  - UTR-based win probability per match
  - Dynamic bracket — future matchups depend on
    earlier round winners
  - Surface adjustment factor (grass/clay/hard)
  - 50,000 iterations default

Key functions:

  utr_win_prob(utr_a: float,
               utr_b: float) -> float
    Win probability from UTR difference.
    P(A beats B) = 1 / (1 + 10^(-(A-B)/2.0))
    The 2.0 divisor is calibrated to UTR scale.

  simulate_match(player_a: str, player_b: str,
                 ratings: dict,
                 surface: str) -> str
    Simulates one match. Returns winner name.
    Applies surface adjustment to ratings.

  simulate_bracket(bracket: dict,
                   ratings: dict,
                   n: int) -> dict
    Runs n Monte Carlo simulations of remaining
    bracket. At each round, determines matchups
    from previous round winners before simulating.
    Returns {player: win_probability}.

  get_remaining_bracket(bracket: dict) -> dict
    Takes current bracket state (with some
    completed matches) and returns the tree of
    remaining possible matchups.

Surface adjustments (approximate Elo equivalents):
  Clay:  serve-heavy players -30 Elo equivalent
  Grass: baseline players -20 Elo equivalent
  Hard:  no adjustment (neutral surface)
"""
