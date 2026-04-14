"""
Fetches chess tournament data from the Lichess broadcast API
(for game results) and Chess.com (for player ratings only).

Lichess broadcast API: https://lichess.org/api/broadcast/{slug}
Chess.com stats API:   https://api.chess.com/pub/player/{username}/stats

Key data contract — all functions return
standardized formats used by chess_simulator.py:

  get_tournament(tournament_id: str) -> dict
    Fetches tournament metadata from Lichess.
    tournament_id may be a Lichess tour ID (e.g. 'BLA70Vds')
    or a slug (e.g. 'fide-candidates-2026-open').
    ID is tried first; if 404, the broadcast list is searched
    for a matching slug.
    Returns: {
      "name": str,
      "id": str,
      "slug": str,
      "status": str,
      "total_rounds": int,
      "rounds_complete": int,
      "rounds": [{"id", "name", "finished", "number"}],
      "players": []   # populated by get_players()
    }

  get_round_games(round_id: str) -> list
    Fetches all completed games for a round via PGN endpoint.
    Returns: [(white_name, black_name, white_score, black_score)]

  get_completed_results(tournament_id: str) -> dict
    Returns: {round_number: [(white, black, ws, bs), ...]}

  get_players(tournament_id: str) -> list
    Extracts sorted list of all player names from round 1.

  get_player_ratings(players: list) -> dict
    Chess.com classical/rapid rating with FIDE scrape fallback.
    Returns: {player_name: elo_rating}

  get_remaining_schedule(tournament_id, completed_results,
                         all_players) -> list
    Full double round-robin minus completed pairs.
"""

import re
import time
import requests
from itertools import permutations

LICHESS_BASE  = "https://lichess.org/api"
CHESSCOM_BASE = "https://api.chess.com/pub"
HEADERS = {
    "User-Agent": "PolymarketBot/1.0 tournament-simulator"
}

# Maps real player names → Chess.com usernames
PLAYER_NAME_TO_USERNAME = {
    "Fabiano Caruana":       "fabianocaruana",
    "Hikaru Nakamura":       "hikaru",
    "Javokhir Sindarov":     "sindarov_j",
    "Praggnanandhaa R":      "rpraggnanandhaa",
    "Anish Giri":            "anishgiri",
    "Wei Yi":                "weiyi_chess",
    "Andrey Esipenko":       "andrey_esipenko",
    "Matthias Bluebaum":     "matthiasbluebaum",
    "Magnus Carlsen":        "magnuscarlsen",
    "Ian Nepomniachtchi":    "lachessis",
    "Gukesh Dommaraju":      "gukeshdommaraju",
    "Alireza Firouzja":      "firouzja2003",
    "Vidit Gujrathi":        "viditchess",
    "Nijat Abasov":          "nijatabasov",
    "Ding Liren":            "dinglirenofficial",
    "Arjun Erigaisi":        "arjunerigaisi",
    "Vincent Keymer":        "vincentkeymer",
    "Nodirbek Abdusattorov": "nodirbek_abdusattorov",
    "Alexei Shirov":         "alexeishipov",
    "Max Warmerdam":         "maxwarmerdam",
    "Jorden van Foreest":    "jordenvanforeest",
}


# ─── Name normalisation ───────────────────────────────────────────────────────

# PGN headers use "Last, First" (Western) or non-standard forms.
# Overrides handle names that don't follow simple "Last, First" → "First Last".
PGN_NAME_OVERRIDES = {
    "Wei, Yi":          "Wei Yi",           # Chinese naming convention
    "Praggnanandhaa, R": "Praggnanandhaa R", # single-letter surname initial
}


def _normalize_pgn_name(name: str) -> str:
    """Converts a PGN player name to the standard 'First Last' form used in
    PLAYER_NAME_TO_USERNAME and throughout the codebase.

    Handles 'Last, First' → 'First Last', with special-case overrides for
    names that don't follow that convention (e.g. 'Wei, Yi' → 'Wei Yi').
    """
    name = name.strip()
    if name in PGN_NAME_OVERRIDES:
        return PGN_NAME_OVERRIDES[name]
    if ", " in name:
        last, first = name.split(", ", 1)
        return f"{first} {last}"
    return name


# ─── Lichess broadcast functions ──────────────────────────────────────────────

def _resolve_broadcast_id(slug_or_id: str) -> str | None:
    """Returns the Lichess tour ID for a given slug or ID.

    Tries the input as a tour ID directly; if that 404s, pages through
    the broadcast list looking for a matching slug.
    Returns the tour ID string, or None if not found.
    """
    # Try direct ID lookup first
    url = f"{LICHESS_BASE}/broadcast/{slug_or_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return slug_or_id   # it's already a valid tour ID
    except Exception:
        pass

    # Search broadcast list by slug (NDJSON, paged)
    print(f"  '{slug_or_id}' not found as tour ID — searching by slug...")
    for page in range(1, 6):
        try:
            r = requests.get(
                f"{LICHESS_BASE}/broadcast",
                params={"page": page},
                headers={**HEADERS, "Accept": "application/x-ndjson"},
                timeout=10,
            )
            r.raise_for_status()
        except Exception:
            break
        lines = [l for l in r.text.strip().split("\n") if l]
        if not lines:
            break
        for line in lines:
            try:
                item = __import__("json").loads(line)
                tour = item.get("tour", {})
                if tour.get("slug") == slug_or_id or tour.get("id") == slug_or_id:
                    return tour["id"]
            except Exception:
                continue

    return None


def get_tournament(tournament_id: str) -> dict:
    """Fetches tournament metadata from the Lichess broadcast API.

    tournament_id may be a Lichess tour ID (e.g. 'BLA70Vds')
    or a slug (e.g. 'fide-candidates-2026-open').
    ID is tried first; if 404, the broadcast list is searched
    for a matching slug.

    Returns standardised dict; returns {} on error.
    """
    tour_id = _resolve_broadcast_id(tournament_id)
    if not tour_id:
        print(f"  Error: could not resolve broadcast '{tournament_id}'")
        return {}

    url = f"{LICHESS_BASE}/broadcast/{tour_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Error fetching broadcast '{tour_id}': {e}")
        return {}

    tour   = data.get("tour", {})
    rounds_raw = data.get("rounds", [])

    rounds = []
    rounds_complete = 0
    for i, r in enumerate(rounds_raw, start=1):
        finished = r.get("finished", False)
        rounds.append({
            "id":       r.get("id", ""),
            "name":     r.get("name", f"Round {i}"),
            "finished": finished,
            "number":   i,
            "url":      r.get("url", ""),   # e.g. lichess.org/broadcast/.../round-1/{id}
        })
        if finished:
            rounds_complete += 1

    # Determine overall status
    if rounds_complete == len(rounds) and len(rounds) > 0:
        status = "finished"
    elif rounds_complete > 0:
        status = "in_progress"
    else:
        status = "registration"

    return {
        "name":            tour.get("name", tournament_id),
        "id":              tour.get("id", ""),
        "slug":            tournament_id,
        "status":          status,
        "total_rounds":    len(rounds),
        "rounds_complete": rounds_complete,
        "rounds":          rounds,
        "players":         [],   # populated by get_players()
    }


def get_round_games(round_url: str) -> list:
    """Fetches all completed games from a Lichess broadcast round.

    round_url is the full Lichess broadcast round URL
    (e.g. https://lichess.org/broadcast/.../round-1/uLCZwqAK).
    Appends '.pgn' to download the PGN file.

    Parses White/Black/Result headers.
    Skips in-progress games (Result = '*').

    Returns [(white_name, black_name, white_score, black_score)].
    """
    pgn_url = round_url.rstrip("/") + ".pgn"
    try:
        resp = requests.get(
            pgn_url,
            headers={**HEADERS, "Accept": "application/x-chess-pgn"},
            timeout=15,
        )
        resp.raise_for_status()
        pgn_text = resp.text
    except Exception as e:
        print(f"  Error fetching round PGN {pgn_url}: {e}")
        return []

    time.sleep(0.5)   # polite pause after each request

    header_pattern = re.compile(r'\[(\w+)\s+"([^"]+)"\]')

    # Split on double-blank-line between games
    game_blocks = re.split(r'\n\n(?=\[Event)', pgn_text.strip())

    results = []
    for block in game_blocks:
        headers = dict(header_pattern.findall(block))

        white  = _normalize_pgn_name(headers.get("White", ""))
        black  = _normalize_pgn_name(headers.get("Black", ""))
        result = headers.get("Result", "*").strip()

        if not white or not black:
            continue
        if result == "*":
            continue   # game still in progress

        if result == "1-0":
            ws, bs = 1.0, 0.0
        elif result == "0-1":
            ws, bs = 0.0, 1.0
        elif result == "1/2-1/2":
            ws, bs = 0.5, 0.5
        else:
            continue   # unrecognised result token

        results.append((white, black, ws, bs))

    return results


def get_completed_results(tournament_id: str) -> dict:
    """Fetches all completed round results grouped by round number.

    Returns {round_number: [(white, black, ws, bs), ...]}
    """
    tournament = get_tournament(tournament_id)
    if not tournament:
        return {}

    completed   = {}
    total_games = 0

    for round_info in tournament["rounds"]:
        if not round_info["finished"]:
            continue
        round_num = round_info["number"]
        games     = get_round_games(round_info["url"])
        if games:
            completed[round_num] = games
            total_games += len(games)

    print(f"  Fetched {len(completed)} completed rounds, {total_games} total games")
    return completed


def get_players(tournament_id: str) -> list:
    """Returns a sorted list of all player names in the tournament.

    Extracts unique player names from round 1 game results.
    """
    tournament = get_tournament(tournament_id)
    if not tournament or not tournament["rounds"]:
        return []

    round1_url = tournament["rounds"][0]["url"]
    games      = get_round_games(round1_url)

    names = set()
    for white, black, _ws, _bs in games:
        names.add(white)
        names.add(black)

    return sorted(names)


# ─── Chess.com rating functions ───────────────────────────────────────────────

def get_player_rating(username: str) -> int | None:
    """Fetches current Elo from Chess.com player stats.

    Tries classical first, falls back to rapid.
    Returns None on failure.
    """
    url = f"{CHESSCOM_BASE}/player/{username}/stats"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        classical = data.get("chess_classical", {})
        if classical:
            rating = classical.get("last", {}).get("rating")
            if rating:
                return int(rating)

        rapid = data.get("chess_rapid", {})
        if rapid:
            rating = rapid.get("last", {}).get("rating")
            if rating:
                return int(rating)

        print(f"  WARNING: no rating found for {username}")
        return None

    except Exception as e:
        print(f"  WARNING: could not fetch rating for {username}: {e}")
        return None


def get_player_ratings(players: list) -> dict:
    """Fetches ratings for a list of real player names.

    Primary:  Chess.com via PLAYER_NAME_TO_USERNAME lookup.
    Fallback: FIDE profile scrape for players in elo_model.FIDE_IDS.

    Returns {real_name: elo_rating}.
    """
    ratings = {}

    for name in players:
        rating = None

        username = PLAYER_NAME_TO_USERNAME.get(name)
        if username:
            rating = get_player_rating(username)
            time.sleep(0.5)

        # FIDE scrape fallback
        if rating is None:
            try:
                from elo_model import scrape_fide_rating, FIDE_IDS
                if name in FIDE_IDS:
                    rating = scrape_fide_rating(FIDE_IDS[name], name=name)
            except Exception:
                pass

        if rating is not None:
            ratings[name] = rating
            print(f"  {name}: {rating}")
        else:
            print(f"  WARNING: no rating found for '{name}', skipping")

    return ratings


# ─── Schedule helper ──────────────────────────────────────────────────────────

def get_remaining_schedule(tournament_id: str,
                           completed_results: dict,
                           all_players: list) -> list:
    """Returns all unplayed games for a round-robin tournament.

    Generates the full double round-robin schedule (every ordered pair),
    subtracts completed pairs, returns remaining (white, black) tuples.
    """
    completed_pairs = set()
    for games in completed_results.values():
        for white, black, _ws, _bs in games:
            completed_pairs.add((white, black))

    full_schedule = [(a, b) for a, b in permutations(all_players, 2)]
    return [(w, b) for w, b in full_schedule if (w, b) not in completed_pairs]
