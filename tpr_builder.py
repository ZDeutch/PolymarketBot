"""
tpr_builder.py — builds tpr_data.json from elite Lichess broadcast PGN data.

Run ONCE manually to build the dataset:
    python tpr_builder.py

Output: tpr_data.json (read by chess_simulator.py at runtime)
Do NOT run automatically.

Algorithm:
  1. Fetch PGN for every round of each elite tournament
     from the Lichess broadcast API.
  2. Filter to games involving at least one 2600+ rated player.
  3. Compute max-likelihood TPR for each player using only
     games against 2600+ opposition.
  4. Compute personal draw rate against 2600+ opposition.
  5. Write results to tpr_data.json.
"""

import requests
import json
import re
import math
import time
from collections import defaultdict


# ─── Constants ────────────────────────────────────────────────────────────────

HEADERS     = {"User-Agent": "PolymarketBot/1.0"}
LICHESS_API = "https://lichess.org/api"

# Major elite tournaments on Lichess 2024-2025.
# Tour IDs extracted from broadcast URLs.
# Format: (tour_slug_or_id, display_name, year)
ELITE_TOURNAMENTS = [
    # 2024 events
    ("tata-steel-masters-2024",          "Tata Steel 2024",        2024),
    ("fide-candidates-2024",              "Candidates 2024",        2024),
    ("norway-chess-2024",                 "Norway Chess 2024",      2024),
    ("sinquefield-cup-2024",              "Sinquefield Cup 2024",   2024),
    ("fide-grand-swiss-2024",             "Grand Swiss 2024",       2024),
    ("fide-world-chess-championship-2024","WCC 2024",               2024),
    # 2025 events
    ("tata-steel-chess-2025--masters",    "Tata Steel 2025",        2025),
    ("superbet-chess-classic-2025",       "Superbet 2025",          2025),
    ("norway-chess-2025",                 "Norway Chess 2025",      2025),
    ("sinquefield-cup-2025",              "Sinquefield Cup 2025",   2025),
    ("fide-grand-swiss-2025",             "Grand Swiss 2025",       2025),
    ("fide-world-cup-2025",               "World Cup 2025",         2025),
]

MIN_OPPONENT_RATING = 2600
MIN_GAMES_FOR_TPR   = 5   # need at least 5 games to compute reliable TPR


# ─── Broadcast resolution ──────────────────────────────────────────────────────

def _resolve_broadcast_id(tour_slug: str) -> str | None:
    """Resolves a Lichess broadcast slug to a tour ID.

    Lichess tour IDs are exactly 8 alphanumeric characters (e.g. '3COxSfdj').
    Anything else is treated as a human-readable slug and searched for in
    the broadcast list.

    Returns the tour ID string, or None if not found.
    """
    # 8-char alphanumeric → treat directly as a tour ID
    if re.match(r'^[A-Za-z0-9]{8}$', tour_slug):
        return tour_slug

    # Otherwise search the broadcast list (NDJSON) for a slug match
    url = f"{LICHESS_API}/broadcast?nb=100"
    try:
        r = requests.get(
            url,
            headers={**HEADERS, "Accept": "application/x-ndjson"},
            timeout=15,
        )
        for line in r.text.strip().split("\n"):
            if not line.strip():
                continue
            obj  = json.loads(line)
            tour = obj.get("tour", obj)
            if tour.get("slug") == tour_slug:
                return tour["id"]

        return None

    except Exception as e:
        print(f"  Error searching broadcasts: {e}")
        return None


# ─── Data fetching ─────────────────────────────────────────────────────────────

def get_tour_rounds(tour_slug: str) -> list:
    """Returns a list of round IDs for a broadcast tour slug.

    Resolves the slug to a Lichess tour ID, then fetches the broadcast
    metadata to extract all round IDs.

    Prints:  "  Found {n} rounds for {tour_slug}"
    On failure prints a warning and returns [].
    """
    tour_id = _resolve_broadcast_id(tour_slug)
    if not tour_id:
        print(f"  WARNING: could not resolve tour '{tour_slug}' — skipping.")
        return []

    url = f"{LICHESS_API}/broadcast/{tour_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  WARNING: could not fetch broadcast '{tour_id}': {e}")
        return []

    rounds    = data.get("rounds", [])
    round_ids = [r["id"] for r in rounds if r.get("id")]
    print(f"  Found {len(round_ids)} rounds for {tour_slug}")
    return round_ids


def get_round_pgn(round_id: str) -> str:
    """Fetches PGN text for a broadcast round.

    URL: https://lichess.org/broadcast/-/-/{round_id}.pgn
    Sleeps 1 second after each request to be polite to Lichess.
    Returns raw PGN text, or "" on error.
    """
    url = f"https://lichess.org/broadcast/-/-/{round_id}.pgn"
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Accept": "application/x-chess-pgn"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"    WARNING: could not fetch PGN for round '{round_id}': {e}")
        return ""
    finally:
        time.sleep(1)


# ─── PGN parsing ──────────────────────────────────────────────────────────────

def parse_pgn_games(pgn_text: str) -> list:
    """Parses PGN text into a list of normalised game dicts.

    Uses regex to extract PGN tag-pair headers. Each game is identified
    by a block beginning with [Event. Games are split on that boundary.

    Skips games where:
      - Result is "*" (in progress / abandoned)
      - Either WhiteElo or BlackElo is missing or non-numeric

    Returns list of dicts:
    {
      "white": str, "black": str,
      "white_elo": int, "black_elo": int,
      "result": str,
      "white_score": float, "black_score": float,
    }
    """
    if not pgn_text.strip():
        return []

    header_re = re.compile(r'\[(\w+)\s+"([^"]+)"\]')

    # Split into individual game blocks at each [Event tag
    game_blocks = re.split(r'(?=\[Event )', pgn_text)

    games = []
    for block in game_blocks:
        block = block.strip()
        if not block:
            continue

        headers = dict(header_re.findall(block))

        white  = headers.get("White", "").strip()
        black  = headers.get("Black", "").strip()
        result = headers.get("Result", "*").strip()

        if not white or not black:
            continue
        if result == "*":
            continue

        # Parse Elo — Lichess may write "?" for unknown
        try:
            white_elo = int(headers["WhiteElo"])
        except (KeyError, ValueError):
            continue
        try:
            black_elo = int(headers["BlackElo"])
        except (KeyError, ValueError):
            continue

        # Convert result to numeric scores
        if result == "1-0":
            white_score, black_score = 1.0, 0.0
        elif result == "0-1":
            white_score, black_score = 0.0, 1.0
        elif result == "1/2-1/2":
            white_score, black_score = 0.5, 0.5
        else:
            continue

        games.append({
            "white":       white,
            "black":       black,
            "white_elo":   white_elo,
            "black_elo":   black_elo,
            "result":      result,
            "white_score": white_score,
            "black_score": black_score,
        })

    return games


# ─── TPR computation ──────────────────────────────────────────────────────────

def compute_tpr(games: list, player_name: str) -> float | None:
    """Computes maximum-likelihood Tournament Performance Rating.

    Based on Steven Pav's ML estimator:
      TPR = argmax_R Σᵢ [ sᵢ·log(E(R,rᵢ)) + (1−sᵢ)·log(1−E(R,rᵢ)) ]

    Where E(R, r) = 1 / (1 + 10^((r − R) / 400))

    Only games against opponents rated >= MIN_OPPONENT_RATING are used.
    Returns None if fewer than MIN_GAMES_FOR_TPR qualifying games exist.

    Uses scipy.optimize.minimize_scalar if available; falls back to
    binary search on the score-equation root otherwise.
    """
    # Collect (opponent_elo, player_score) pairs
    pairs = []
    for g in games:
        if g["white"] == player_name:
            opp_elo = g["black_elo"]
            score   = g["white_score"]
        elif g["black"] == player_name:
            opp_elo = g["white_elo"]
            score   = g["black_score"]
        else:
            continue

        if opp_elo is None or opp_elo < MIN_OPPONENT_RATING:
            continue
        pairs.append((opp_elo, score))

    if len(pairs) < MIN_GAMES_FOR_TPR:
        return None

    def neg_log_likelihood(R: float) -> float:
        total = 0.0
        for r_i, s_i in pairs:
            E = 1.0 / (1.0 + 10.0 ** ((r_i - R) / 400.0))
            E = max(1e-9, min(1.0 - 1e-9, E))   # guard against log(0)
            total += s_i * math.log(E) + (1.0 - s_i) * math.log(1.0 - E)
        return -total

    # Try scipy first
    try:
        from scipy.optimize import minimize_scalar
        result = minimize_scalar(
            neg_log_likelihood, bounds=(1800, 3200), method="bounded"
        )
        return round(result.x, 1)
    except ImportError:
        pass

    # Binary search fallback.
    # The gradient dL/dR = Σ (sᵢ − E(R,rᵢ)) · ln(10)/400 equals zero when
    # total expected score = total actual score.  Since total_E increases
    # monotonically with R, we can bisect on (total_E − total_score).
    total_score = sum(s for _, s in pairs)

    lo, hi = 1800.0, 3200.0
    for _ in range(60):   # 60 iterations → precision < 0.01 Elo
        mid     = (lo + hi) / 2.0
        total_E = sum(
            1.0 / (1.0 + 10.0 ** ((r_i - mid) / 400.0))
            for r_i, _ in pairs
        )
        if total_E < total_score:
            lo = mid
        else:
            hi = mid

    return round((lo + hi) / 2.0, 1)


def compute_draw_rate(games: list, player_name: str) -> float | None:
    """Computes the player's personal draw rate against 2600+ opposition.

    Returns draws / total_games for qualifying games.
    Returns None if fewer than 5 qualifying games.
    """
    scores = []
    for g in games:
        if g["white"] == player_name:
            opp_elo = g["black_elo"]
            score   = g["white_score"]
        elif g["black"] == player_name:
            opp_elo = g["white_elo"]
            score   = g["black_score"]
        else:
            continue

        if opp_elo is not None and opp_elo >= MIN_OPPONENT_RATING:
            scores.append(score)

    if len(scores) < 5:
        return None

    draws = sum(1 for s in scores if s == 0.5)
    return round(draws / len(scores), 4)


# ─── Player stats aggregation ─────────────────────────────────────────────────

def build_player_stats(all_games: list) -> dict:
    """Computes per-player TPR, draw rate, game count, and avg opponent.

    Returns:
    {
      player_name: {
        "tpr":          float | None,
        "draw_rate":    float | None,
        "elite_games":  int,
        "avg_opponent": float | None,
      }
    }
    """
    # Collect all unique player names
    player_names: set[str] = set()
    for g in all_games:
        player_names.add(g["white"])
        player_names.add(g["black"])

    stats = {}
    for player in sorted(player_names):
        tpr       = compute_tpr(all_games, player)
        draw_rate = compute_draw_rate(all_games, player)

        elite_games  = 0
        opp_ratings  = []
        for g in all_games:
            if g["white"] == player:
                opp_elo = g["black_elo"]
            elif g["black"] == player:
                opp_elo = g["white_elo"]
            else:
                continue
            elite_games += 1
            if opp_elo is not None and opp_elo >= MIN_OPPONENT_RATING:
                opp_ratings.append(opp_elo)

        avg_opponent = (
            round(sum(opp_ratings) / len(opp_ratings), 1)
            if opp_ratings else None
        )

        stats[player] = {
            "tpr":          tpr,
            "draw_rate":    draw_rate,
            "elite_games":  elite_games,
            "avg_opponent": avg_opponent,
        }

    return stats


# ─── Main ─────────────────────────────────────────────────────────────────────

def run() -> None:
    """Fetches PGN data, computes TPR, writes tpr_data.json."""
    from datetime import datetime

    print("Building elite TPR database...")
    print(f"Fetching games from {len(ELITE_TOURNAMENTS)} tournaments")

    all_games: list = []
    for tour_slug, name, year in ELITE_TOURNAMENTS:
        print(f"\n[{year}] {name}")
        rounds = get_tour_rounds(tour_slug)
        for round_id in rounds:
            pgn   = get_round_pgn(round_id)
            games = parse_pgn_games(pgn)
            # Keep games where at least one player is rated 2600+
            elite = [
                g for g in games
                if (g["white_elo"] and g["white_elo"] >= MIN_OPPONENT_RATING)
                or (g["black_elo"] and g["black_elo"] >= MIN_OPPONENT_RATING)
            ]
            all_games.extend(elite)
        print(f"  Running total: {len(all_games)} elite games")

    print("\nComputing TPR for all players...")
    stats = build_player_stats(all_games)

    # Filter to players with enough data
    qualified = {
        name: data for name, data in stats.items()
        if data["elite_games"] >= MIN_GAMES_FOR_TPR
    }

    print(f"\nPlayers with TPR data: {len(qualified)}")
    print("Top 20 by TPR:")
    top20 = sorted(
        [(n, d) for n, d in qualified.items() if d["tpr"] is not None],
        key=lambda x: x[1]["tpr"],
        reverse=True,
    )[:20]
    for player, data in top20:
        dr_str = f"{data['draw_rate']:.2f}" if data["draw_rate"] is not None else " N/A"
        print(
            f"  {player:<30}  TPR {data['tpr']:>4.0f}  "
            f"draw {dr_str}  games {data['elite_games']}"
        )

    output = {
        "generated_at":        datetime.now().isoformat(),
        "tournaments_scanned": len(ELITE_TOURNAMENTS),
        "total_elite_games":   len(all_games),
        "players":             qualified,
    }

    with open("tpr_data.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ tpr_data.json written with {len(qualified)} players")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from datetime import datetime
    run()
