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

import random
import math

# ─── Constants ────────────────────────────────────────────────────────────────

BASE_ELO       = 1500
ELO_SCALE      = 173.7   # from Tennis Abstract ATP Elo calibration

GRAND_SLAM_ROUNDS = 7    # 128-draw: R128→F
MASTERS_ROUNDS    = 6    # 96-draw (byes in R1)

SURFACE_BONUS = {
    "grass": 40,   # Elo points for known serve/grass specialists
    "clay":   0,
    "hard":   0,
}

# Players who receive the grass bonus
GRASS_SPECIALISTS = {
    "Novak Djokovic",
    "Roger Federer",
    "Pete Sampras",
    "Nick Kyrgios",
    "Marin Cilic",
    "Milos Raonic",
    "Jannik Sinner",      # Wimbledon 2025 champion
}


# ─── Core probability functions ───────────────────────────────────────────────

def atp_points_to_elo(points: int) -> float:
    """Converts ATP ranking points to an Elo-equivalent rating.

    Formula from Tennis Abstract:
      BASE_ELO + ELO_SCALE * ln(points)

    Calibration:
      ~10 000 pts (world #1)  → ~2500 Elo
      ~1 000 pts  (top ~30)   → ~2100 Elo
      ~100 pts    (top ~100)  → ~1700 Elo
    """
    return BASE_ELO + ELO_SCALE * math.log(max(points, 1))


def match_win_prob(elo_a: float, elo_b: float) -> float:
    """Standard Elo win probability: P(A beats B)."""
    return 1.0 / (1.0 + 10.0 ** ((elo_b - elo_a) / 400.0))


# ─── Single match simulation ──────────────────────────────────────────────────

def simulate_match(player_a: str,
                   player_b: str,
                   ratings: dict,
                   surface: str = "hard") -> str:
    """Simulates one match. Returns the winner's name.

    Converts ATP points → Elo, applies grass-court bonus for
    known specialists, then samples from Elo win probability.
    """
    pts_a = ratings.get(player_a, 100)
    pts_b = ratings.get(player_b, 100)

    elo_a = atp_points_to_elo(pts_a)
    elo_b = atp_points_to_elo(pts_b)

    if surface == "grass":
        bonus = SURFACE_BONUS["grass"]
        if player_a in GRASS_SPECIALISTS:
            elo_a += bonus
        if player_b in GRASS_SPECIALISTS:
            elo_b += bonus

    p_a = match_win_prob(elo_a, elo_b)
    return player_a if random.random() < p_a else player_b


# ─── Bracket construction ─────────────────────────────────────────────────────

def build_bracket_slots(players: list,
                        seeds: dict,
                        draw_size: int = 128) -> list:
    """Places players into bracket slots following ATP-standard seeding.

    For a 128-draw:
      Slot 0   (index 0):    Seed 1
      Slot 128 (index -1):   Seed 2
      Slots 63/64:           Seeds 3/4 (randomly assigned)
      Slots 31/32/95/96:     Seeds 5-8 (randomly assigned, one per quarter)
      Slots 15/16/47/48/79/80/111/112: Seeds 9-16 (one per eighth)
      Remaining:             unseeded players randomly distributed

    Returns list of player names in bracket order.
    """
    n    = min(draw_size, len(players))
    slots = [None] * n

    def seed_key(p):
        s = seeds.get(p)
        return s if s is not None else 9999

    # Scale a canonical 128-draw slot position to the actual draw size
    def sp(pos128):
        return min(int(pos128 * n / 128), n - 1)

    # Canonical slot positions for each seed number (128-draw)
    canonical_128 = {
        1:  [0],
        2:  [127],
        3:  [63],   4:  [64],           # randomised below
        5:  [31],   6:  [96],           # randomised below
        7:  [32],   8:  [95],
        9:  [15],   10: [112],          # randomised below
        11: [48],   12: [79],
        13: [16],   14: [111],
        15: [47],   16: [80],
    }

    placed: set[str] = set()

    # Build group lists and shuffle within tier for randomised draw
    def tier(lo, hi):
        grp = sorted([p for p in players if lo <= seed_key(p) <= hi],
                     key=seed_key)
        random.shuffle(grp)
        return grp

    tiers = [tier(1, 1), tier(2, 2), tier(3, 4), tier(5, 8),
             tier(9, 16), tier(17, 32)]

    # Canonical positions lists (shuffled within tier)
    tier_positions = [
        [sp(0)],                             # seed 1
        [sp(127)],                           # seed 2
        [sp(63), sp(64)],                    # seeds 3-4
        [sp(31), sp(96), sp(32), sp(95)],    # seeds 5-8
        [sp(15), sp(112), sp(48), sp(79),    # seeds 9-16
         sp(16), sp(111), sp(47), sp(80)],
        [],                                  # seeds 17-32: fill freely
    ]
    for pos_list in tier_positions:
        random.shuffle(pos_list)

    def place_at(target: int, player: str) -> bool:
        """Place player at target or nearest free slot. Returns True if placed."""
        for delta in range(n):
            for pos in [target + delta, target - delta]:
                if 0 <= pos < n and slots[pos] is None:
                    slots[pos] = player
                    placed.add(player)
                    return True
        return False

    for grp_players, positions in zip(tiers, tier_positions):
        for i, player in enumerate(grp_players):
            if i < len(positions):
                place_at(positions[i], player)
            else:
                # No canonical position — find any free slot
                for pos in range(n):
                    if slots[pos] is None:
                        slots[pos] = player
                        placed.add(player)
                        break

    # Fill remaining slots with unplaced players (unseeded or >seed 32)
    remaining = [p for p in players if p not in placed]
    random.shuffle(remaining)
    ri = iter(remaining)
    for i in range(n):
        if slots[i] is None:
            try:
                slots[i] = next(ri)
            except StopIteration:
                break

    return [p for p in slots if p is not None]


# ─── Tournament simulation ────────────────────────────────────────────────────

def simulate_tournament(players: list,
                        ratings: dict,
                        seeds: dict,
                        bracket: dict,
                        surface: str = "hard",
                        n: int = 50000) -> dict:
    """Runs n Monte Carlo simulations of the remaining bracket.

    players:  full field (list of str)
    ratings:  {player_name: atp_points}
    seeds:    {player_name: seed_number | None}
    bracket:  from tennis_fetcher.get_bracket() — completed matches only
              {round_num: [{"player_a","player_b","winner","completed"}]}
    surface:  "hard" | "clay" | "grass"
    n:        number of simulations

    Algorithm:
      1. From completed bracket, find the highest fully-complete round.
      2. Build ordered alive list from that round's winners (preserving
         bracket section order). Pre-tournament → seeded draw.
      3. For each simulation: pair alive[i] vs alive[i+1], halving
         the field each round until one champion remains.

    Returns {player_name: win_probability}.
    """
    win_counts = {p: 0 for p in players}

    # ── Build the starting alive list ────────────────────────────────────
    # Find the highest round where every match has a winner
    max_complete = 0
    for r in sorted(bracket.keys()):
        if bracket[r] and all(m.get("completed") and m.get("winner")
                               for m in bracket[r]):
            max_complete = r
        else:
            break   # first incomplete round stops search

    if max_complete > 0:
        # Ordered winners from the last fully complete round;
        # their order encodes bracket section structure.
        alive_base = [m["winner"] for m in bracket[max_complete]
                      if m.get("winner")]
    else:
        # Pre-tournament: build seeded bracket draw
        draw_size  = 128 if len(players) >= 65 else 64
        alive_base = build_bracket_slots(players, seeds, draw_size)

    # ── Check for a partially complete round above max_complete ──────────
    partial_round_num = max_complete + 1
    partial_matches   = bracket.get(partial_round_num, [])
    has_partial       = bool(partial_matches) and not all(
        m.get("completed") and m.get("winner") for m in partial_matches
    )

    # ── Monte Carlo ───────────────────────────────────────────────────────
    for _ in range(n):
        # Resolve the partial round (some matches done, some not)
        if has_partial:
            round_players: list[str] = []
            for idx, match in enumerate(partial_matches):
                if match.get("completed") and match.get("winner"):
                    round_players.append(match["winner"])
                else:
                    # Pair from alive_base in bracket order
                    ia, ib = 2 * idx, 2 * idx + 1
                    if ia < len(alive_base) and ib < len(alive_base):
                        winner = simulate_match(
                            alive_base[ia], alive_base[ib], ratings, surface
                        )
                    elif ia < len(alive_base):
                        winner = alive_base[ia]    # bye
                    else:
                        continue
                    round_players.append(winner)
        else:
            round_players = list(alive_base)

        # Simulate remaining rounds (halving the field each time)
        while len(round_players) > 1:
            next_round: list[str] = []
            for i in range(0, len(round_players), 2):
                if i + 1 < len(round_players):
                    winner = simulate_match(
                        round_players[i], round_players[i + 1],
                        ratings, surface
                    )
                    next_round.append(winner)
                else:
                    next_round.append(round_players[i])   # odd player: bye
            round_players = next_round

        if round_players:
            champion = round_players[0]
            if champion in win_counts:
                win_counts[champion] += 1

    return {p: round(win_counts[p] / n, 4) for p in players}
