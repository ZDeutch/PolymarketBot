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

HEADERS = {"User-Agent": "PolymarketBot/1.0"}

# Hardcoded round IDs for each elite tournament.
# Round IDs are the 8-char Lichess broadcast round identifiers found in
# the broadcast URL: lichess.org/broadcast/-/{round-name}/{round_id}
# Notes:
#   - Sinquefield Cup 2024/2025 omitted — not on Lichess (USCF broadcast ban)
#   - WCC 2024 omitted — only 14 classical games, insufficient data
#   - Round lists are partial where only some IDs were recoverable;
#     partial data is still useful for TPR estimation.
HARDCODED_ROUNDS = {
    "Tata Steel 2024": [
        "iSglSzjv", "haD9x5TT", "OrHsLFq5",
        "msGCthzJ", "cmDGoJr1", "ubgeWaHz",
        "k0StVoen", "EDCwDiqA", "L43YRQWv",
    ],
    "Candidates 2024": [
        "AjqSsU1w", "GenKIJ8A", "xQgaUu2y",
        "CPS9dENa", "MDiLWQ5M", "nUycmG6L",
        "vfqUR38R", "eJghIBZe", "S4zisI6M",
    ],
    "Norway Chess 2024": [
        "I9TLGEOt", "sbOHYOVj", "xmZcMs9U",
        "Whq5YPU7", "Qvlkp2yF", "C5Zzd9mM",
    ],
    "Tata Steel 2025": [
        "FRGTkE8Z", "LPs7X3dM", "T4KlhCEf",
        "TU7qC17C", "pdNmUOnu", "9nR8UQz9",
        "W4XtM3HF", "4qFZzDfZ",
    ],
    "Norway Chess 2025": [
        "elkTUv1R", "8Ma8Q5pQ", "4MpGqf5j",
    ],
    "Grand Swiss 2025": [
        "xSCoiNg0", "UnZivDF9", "gLwN3kib",
        "zmaKVsPL", "ADzdjVmn", "iAnC0jAl",
        "xtmbmvSP", "FpwTKfvI",
    ],
    "Candidates 2026": [
        # Populated at runtime via get_tournament("BLA70Vds") (direct tour ID)
    ],
    "Sigeman 2026": [
        # Add round IDs as each round is broadcast on Lichess.
        # Find IDs in the broadcast URL:
        #   lichess.org/broadcast/-/{round-name}/{round_id}
        # e.g. "will-magnus-carlsen-win..." → round_id is the 8-char suffix
    ],
}

MIN_OPPONENT_RATING = 2600
MIN_GAMES_FOR_TPR   = 5   # need at least 5 games to compute reliable TPR


# ─── Data fetching ─────────────────────────────────────────────────────────────

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


# ─── Name normalisation ───────────────────────────────────────────────────────

# Maps PGN "Last, First" variants → canonical "First Last" form used
# throughout the codebase.  Keys are exact strings as they appear in
# Lichess broadcast PGN headers.
_NAME_REPLACEMENTS = {
    "Gukesh, D":               "Gukesh D",
    "Praggnanandhaa, R":       "Praggnanandhaa R",
    "R Praggnanandhaa":        "Praggnanandhaa R",
    "Dommaraju, Gukesh":       "Gukesh D",
    "Nepomniachtchi, Ian":     "Ian Nepomniachtchi",
    "Firouzja, Alireza":       "Alireza Firouzja",
    "Caruana, Fabiano":        "Fabiano Caruana",
    "Nakamura, Hikaru":        "Hikaru Nakamura",
    "Giri, Anish":             "Anish Giri",
    "Abdusattorov, Nodirbek":  "Nodirbek Abdusattorov",
    "Erigaisi, Arjun":         "Arjun Erigaisi",
    "Keymer, Vincent":         "Vincent Keymer",
    "Sindarov, Javokhir":      "Javokhir Sindarov",
    "Esipenko, Andrey":        "Andrey Esipenko",
    "Bluebaum, Matthias":      "Matthias Bluebaum",
    "Wei, Yi":                 "Wei Yi",
    "Erigaisi Arjun":          "Arjun Erigaisi",
    "Abdusattorov Nodirbek":   "Nodirbek Abdusattorov",
    "Keymer Vincent":          "Vincent Keymer",
    "Tabatabaei M. Amin":      "M. Amin Tabatabaei",
    # Sigeman 2026 players
    "Grandelius, Nils":        "Nils Grandelius",
    "Woodward, Andy":          "Andy Woodward",
    "Zhu, Jiner":              "Zhu Jiner",   # keep Chinese surname-first order
    "Van Foreest, Jorden":     "Jorden van Foreest",
    "van Foreest, Jorden":     "Jorden van Foreest",
    "Erdogmus, Yagiz Kaan":    "Yagiz Kaan Erdogmus",
}


def normalize_name(name: str) -> str:
    """Normalises a player name from PGN to canonical form.

    1. Checks the explicit _NAME_REPLACEMENTS lookup first.
    2. Falls back to reversing "Last, First" → "First Last"
       for any name containing exactly one comma.
    """
    # Explicit replacements first
    if name in _NAME_REPLACEMENTS:
        return _NAME_REPLACEMENTS[name]

    # General "Last, First" → "First Last" inversion
    # Only apply if exactly one comma is present
    parts = name.split(",")
    if len(parts) == 2:
        last  = parts[0].strip()
        first = parts[1].strip()
        if first and last:
            return f"{first} {last}"

    return name


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

        white  = normalize_name(headers.get("White", "").strip())
        black  = normalize_name(headers.get("Black", "").strip())
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
    print(f"Fetching games from {len(HARDCODED_ROUNDS)} tournaments")

    # ── Auto-fetch Candidates 2026 round IDs from chess_fetcher ───────────────
    # Uses direct tour ID BLA70Vds (slug-based lookup fails for archived tours)
    print("\nFetching Candidates 2026 round IDs...")
    try:
        from fetchers.chess_fetcher import get_tournament
        t = get_tournament("BLA70Vds")
        if t and t.get("rounds"):
            c2026_ids = [r["id"] for r in t["rounds"] if r.get("finished")]
            HARDCODED_ROUNDS["Candidates 2026"] = c2026_ids
            print(f"  Got {len(c2026_ids)} completed rounds")
        else:
            HARDCODED_ROUNDS["Candidates 2026"] = []
            print("  Could not fetch — skipping")
    except Exception as e:
        print(f"  Error: {e} — skipping Candidates 2026")

    all_games: list = []
    for tour_name, round_ids in HARDCODED_ROUNDS.items():
        print(f"\n{tour_name} ({len(round_ids)} rounds)")
        tour_games = 0
        for round_id in round_ids:
            pgn = get_round_pgn(round_id)
            if not pgn:
                continue
            games = parse_pgn_games(pgn)
            # Keep games where at least one player is rated 2600+
            elite = [
                g for g in games
                if (g["white_elo"] and g["white_elo"] >= MIN_OPPONENT_RATING)
                or (g["black_elo"] and g["black_elo"] >= MIN_OPPONENT_RATING)
            ]
            all_games.extend(elite)
            tour_games += len(elite)
        print(f"  {tour_games} elite games from this tournament")
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
        "tournaments_scanned": len(HARDCODED_ROUNDS),
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
