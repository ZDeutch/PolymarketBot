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
    Chess.com classical/rapid rating with hardcoded fallback.
    Returns: {player_name: elo_rating}

  get_remaining_schedule(tournament_id, completed_results,
                         all_players) -> list
    Full double round-robin minus completed pairs.
"""

import re
import json
import time
import requests
from itertools import permutations

LICHESS_BASE  = "https://lichess.org/api"
CHESSCOM_BASE = "https://api.chess.com/pub"
HEADERS = {
    "User-Agent": "PolymarketBot/1.0 tournament-simulator"
}

# Maps real player names → Chess.com usernames.
# Includes PGN name variants (Last, First reversed; diacritics; etc.)
# so that names extracted directly from PGN headers resolve correctly.
PLAYER_NAME_TO_USERNAME = {
    # Candidates 2026 players
    # Caruana and Nakamura have working Chess.com classical ratings.
    # Giri, Sindarov, Wei Yi removed — Chess.com returns stale/wrong values;
    # Praggnanandhaa, Esipenko removed — Chess.com returns sub-2500; all fall to KNOWN_RATINGS.
    "Fabiano Caruana":       "fabianocaruana",
    "Hikaru Nakamura":       "hikaru",
    "Praggnanandhaa R":      "rpraggnanandhaa",
    "Praggnanandhaa":        "rpraggnanandhaa",
    "R Praggnanandhaa":      "rpraggnanandhaa",
    "Matthias Bluebaum":     "matthiasbluebaum",
    "Matthias Blübaum":      "matthiasbluebaum",

    # Sigeman 2026 players (new)
    "Nils Grandelius":            "grandelius",
    "Andy Woodward":              "andywoodward",
    # Zhu Jiner — Chess.com username unconfirmed; falls through to KNOWN_RATINGS

    # Tata Steel / other elite players
    # Gukesh omitted — Chess.com returns online rating (too low); falls through to KNOWN_RATINGS
    # Carlsen omitted — Chess.com classical is ~106 pts above FIDE OTB; falls through to KNOWN_RATINGS
    # Firouzja omitted — Chess.com classical is ~85 pts above FIDE OTB; falls through to KNOWN_RATINGS
    # Keymer omitted — Chess.com classical is ~29 pts below FIDE OTB; falls through to KNOWN_RATINGS
    "Arjun Erigaisi":             "arjunerigaisi",
    "Erigaisi Arjun":             "arjunerigaisi",
    "Nodirbek Abdusattorov":      "abdusattorov",
    "Abdusattorov Nodirbek":      "abdusattorov",
    "Jorden van Foreest":         "jordanvanforeest",
    "Jorden Van Foreest":         "jordanvanforeest",
    "Ian Nepomniachtchi":         "lachessis",
    "Nepomniachtchi Ian":         "lachessis",
    "Vidit Gujrathi":             "viditchess",
    "Nijat Abasov":               "nijatabasov",
    "Abasov Nijat":               "nijatabasov",
    "Hans Moke Niemann":          "hansniemann",
    "Niemann Hans Moke":          "hansniemann",
    "Vladimir Fedoseev":          "v_fedoseev",
    "Fedoseev Vladimir":          "v_fedoseev",
    "Chithambaram VR. Aravindh":  "aravindh_chithambaram",
    "Aravindh Chithambaram":      "aravindh_chithambaram",
    "Thai Dai Van Nguyen":        "thaidaiannguyen",
    "Nguyen Thai Dai Van":        "thaidaiannguyen",
    "Yagiz Kaan Erdogmus":        "yagiz_kaan_erdogmus",
    "Erdogmus Yagiz Kaan":        "yagiz_kaan_erdogmus",
    "Max Warmerdam":              "maxwarmerdam",
    "Warmerdam Max":              "maxwarmerdam",
    "Alexei Shirov":              "alexeishipov",
    "Ding Liren":                 "dinglirenofficial",
}


# ─── Name normalisation ───────────────────────────────────────────────────────

# PGN headers use "Last, First" (Western) or non-standard forms.
# Overrides handle names that don't follow simple "Last, First" → "First Last".
PGN_NAME_OVERRIDES = {
    "Wei, Yi":           "Wei Yi",            # Chinese naming convention
    "Praggnanandhaa, R": "Praggnanandhaa R",  # single-letter surname initial
    "Zhu, Jiner":        "Zhu Jiner",         # Chinese: keep surname-first order
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

def _resolve_broadcast_id(tournament_id: str) -> str | None:
    """Returns the Lichess tour ID for a given tour ID or slug.

    Lichess tour IDs are always exactly 8 alphanumeric characters
    (e.g. '3COxSfdj'). Anything else is treated as a slug and
    searched for in the broadcast list.

    Returns the tour ID string, or None if not found.
    """
    # 8-char alphanumeric → treat directly as a tour ID
    if re.match(r'^[A-Za-z0-9]{8}$', tournament_id):
        return tournament_id

    # Otherwise search the broadcast list (NDJSON) for a slug match
    url = f"{LICHESS_BASE}/broadcast?nb=100"
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
            if tour.get("slug") == tournament_id:
                return tour["id"]

        print(f"  '{tournament_id}' not found in broadcast list.")
        print(f"  Tip: use the 8-char tour ID from the Lichess URL instead.")
        print(f"  e.g. lichess.org/broadcast/tata-steel-chess-2026--masters/3COxSfdj")
        print(f"       tour ID = '3COxSfdj'")
        return None

    except Exception as e:
        print(f"  Error searching broadcasts: {e}")
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


def get_round_games(round_id: str) -> list:
    """Fetches all completed games from a Lichess broadcast round.

    round_id is the 8-char Lichess round ID (e.g. 'uLCZwqAK').
    Lichess routes by ID — the slug portions of the URL are ignored,
    so placeholder dashes are used to construct the PGN endpoint:
      https://lichess.org/broadcast/-/-/{round_id}.pgn

    Parses White/Black/Result headers.
    Skips in-progress games (Result = '*').

    Returns [(white_name, black_name, white_score, black_score)].
    """
    pgn_url = f"https://lichess.org/broadcast/-/-/{round_id}.pgn"
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
        games     = get_round_games(round_info["id"])
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

    round1_id = tournament["rounds"][0]["id"]
    games     = get_round_games(round1_id)

    names = set()
    for white, black, _ws, _bs in games:
        names.add(white)
        names.add(black)

    return sorted(names)


# ─── Chess.com rating functions ───────────────────────────────────────────────

_CHESSCOM_KEY = {
    "classical": "chess_classical",
    "rapid":     "chess_rapid",
    "blitz":     "chess_blitz",
}
_CHESSCOM_FALLBACK = {
    "classical": "chess_rapid",
    "rapid":     "chess_classical",
    "blitz":     "chess_rapid",
}


def get_player_rating(username: str, fmt: str = "classical") -> int | None:
    """Fetches current Elo from Chess.com player stats for the given format.

    Tries the primary format key first, then the fallback key.
    Validates rating > 2500 — anything lower is likely stale/wrong for a top GM.
    Returns None on failure or implausibly low rating.
    """
    primary  = _CHESSCOM_KEY.get(fmt, "chess_classical")
    fallback = _CHESSCOM_FALLBACK.get(fmt, "chess_rapid")

    url = f"{CHESSCOM_BASE}/player/{username}/stats"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        for key in (primary, fallback):
            rating = data.get(key, {}).get("last", {}).get("rating")
            if rating and int(rating) > 2500:
                return int(rating)

        return None

    except Exception as e:
        print(f"  WARNING: could not fetch rating for {username}: {e}")
        return None


# Hardcoded fallback ratings for players not reachable via Chess.com
# (e.g. missing classical games, unconfirmed usernames).
# Sourced from FIDE live ratings (post-Candidates 2026) — April 23, 2026.
# Rounded to nearest integer; official May 2026 list drops May 1.
KNOWN_RATINGS = {
    # ── Sigeman 2026 ──────────────────────────────────────────────────────────
    "Magnus Carlsen":             2840,   # live #1 (unchanged)
    "Nodirbek Abdusattorov":      2780,   # live #4 (unchanged)
    "Abdusattorov Nodirbek":      2780,
    "Arjun Erigaisi":             2751,   # live #11 (unchanged)
    "Erigaisi Arjun":             2751,
    "Jorden van Foreest":         2736,   # live #15 (unchanged)
    "Jorden Van Foreest":         2736,
    "Nils Grandelius":            2662,   # live #52 (unchanged)
    "Yagiz Kaan Erdogmus":        2709,   # live #30 (was 2687, +22 post-Candidates)
    "Erdogmus Yagiz Kaan":        2709,
    "Andy Woodward":              2635,   # live #91 (unchanged)
    "Woodward Andy":              2635,
    "Zhu Jiner":                  2554,   # April 2026 FIDE (not in live top-100)
    # ── Candidates 2026 ───────────────────────────────────────────────────────
    "Fabiano Caruana":            2788,   # live #3 (was 2793, −5)
    "Hikaru Nakamura":            2792,   # live #2 (was 2810, −18)
    "Anish Giri":                 2767,   # live #6 (was 2753, +14)
    "Wei Yi":                     2753,   # live #10 (was 2754, −1)
    "Javokhir Sindarov":          2776,   # live #5 (was 2745, +31 — won Candidates!)
    "Praggnanandhaa R":           2733,   # live #16 (was 2741, −8)
    "Praggnanandhaa":             2733,
    "R Praggnanandhaa":           2733,
    "Andrey Esipenko":            2684,   # live #40 (was 2698, −14)
    # ── Norway Chess 2026 ────────────────────────────────────────────────────
    "Wesley So":                  2754,   # live #9 (classical)
    "Alireza Firouzja":           2759,   # live #8 (classical)
    "Firouzja Alireza":           2759,
    # ── Other elite players ───────────────────────────────────────────────────
    "Gukesh D":                   2732,   # live #17 (was 2783, −51 post-WCC title defense)
    "Gukesh Dommaraju":           2732,
    "D Gukesh":                   2732,
    "Vincent Keymer":             2762,   # live #7 (was 2726, +36)
    "Chithambaram VR. Aravindh":  2693,   # live #37 (was 2603, +90!)
    "Aravindh Chithambaram":      2693,
    "Hans Moke Niemann":          2742,   # live #12 (was 2663, +79!)
    "Niemann Hans Moke":          2742,
    "Vladimir Fedoseev":          2703,   # live #33 (was 2674, +29)
    "Fedoseev Vladimir":          2703,
    "Matthias Bluebaum":          2696,   # live #34 (was 2700, −4)
    "Matthias Blübaum":           2696,
    "Thai Dai Van Nguyen":        2633,   # live #95 (was 2612, +21)
    "Nguyen Thai Dai Van":        2633,
    "Max Warmerdam":              2598,
    "Warmerdam Max":              2598,
}

# FIDE live rapid ratings — April 23, 2026.
KNOWN_RATINGS_RAPID = {
    "Magnus Carlsen":             2832,   # rapid #1
    "Arjun Erigaisi":             2741,   # rapid #4
    "Erigaisi Arjun":             2741,
    "Hikaru Nakamura":            2742,   # rapid #3
    "Fabiano Caruana":            2727,   # rapid #9
    "Nodirbek Abdusattorov":      2703,   # rapid #16
    "Abdusattorov Nodirbek":      2703,
    "Javokhir Sindarov":          2727,   # rapid #10
    "Wei Yi":                     2726,   # rapid #12
    "Anish Giri":                 2689,   # rapid #22
    "Praggnanandhaa R":           2663,   # rapid #33
    "Praggnanandhaa":             2663,
    "R Praggnanandhaa":           2663,
    "Andrey Esipenko":            2657,   # rapid #36
    "Nils Grandelius":            2611,   # rapid #82
    "Gukesh D":                   2682,   # rapid #27
    "Gukesh Dommaraju":           2682,
    "D Gukesh":                   2682,
    "Vincent Keymer":             2627,   # rapid #58
    "Vladimir Fedoseev":          2690,   # rapid #20
    "Fedoseev Vladimir":          2690,
    "Hans Moke Niemann":          2646,   # rapid #40
    "Niemann Hans Moke":          2646,
    "Wesley So":                  2705,   # rapid #15
    "Alireza Firouzja":           2755,   # rapid #2
    "Firouzja Alireza":           2755,
}

# FIDE live blitz ratings — April 23, 2026.
KNOWN_RATINGS_BLITZ = {
    "Magnus Carlsen":             2869,   # blitz #1
    "Hikaru Nakamura":            2838,   # blitz #2
    "Nodirbek Abdusattorov":      2785,   # blitz #6
    "Abdusattorov Nodirbek":      2785,
    "Arjun Erigaisi":             2776,   # blitz #7
    "Erigaisi Arjun":             2776,
    "Fabiano Caruana":            2749,   # blitz #12
    "Wei Yi":                     2698,   # blitz #23
    "Praggnanandhaa R":           2698,   # blitz #22
    "Praggnanandhaa":             2698,
    "R Praggnanandhaa":           2698,
    "Javokhir Sindarov":          2662,   # blitz #40
    "Andrey Esipenko":            2651,   # blitz #44
    "Anish Giri":                 2666,   # blitz #38
    "Jorden van Foreest":         2690,   # blitz #27
    "Jorden Van Foreest":         2690,
    "Gukesh D":                   2646,   # blitz #49
    "Gukesh Dommaraju":           2646,
    "D Gukesh":                   2646,
    "Vladimir Fedoseev":          2756,   # blitz #11
    "Fedoseev Vladimir":          2756,
    "Matthias Bluebaum":          2634,   # blitz #61
    "Matthias Blübaum":           2634,
    "Vincent Keymer":             2621,   # blitz #70
    "Hans Moke Niemann":          2699,   # blitz #21
    "Niemann Hans Moke":          2699,
    "Wesley So":                  2798,   # blitz #3
    "Alireza Firouzja":           2796,   # blitz #4
    "Firouzja Alireza":           2796,
}

_KNOWN_RATINGS_BY_FORMAT = {
    "classical": KNOWN_RATINGS,
    "rapid":     KNOWN_RATINGS_RAPID,
    "blitz":     KNOWN_RATINGS_BLITZ,
}


def get_player_ratings(players: list, fmt: str = "classical") -> dict:
    """Fetches ratings for a list of real player names.

    fmt controls which rating type to prefer: 'classical', 'rapid', or 'blitz'.

    Tries in order:
      1. Chess.com (primary format, then fallback format — must be > 2500)
      2. Format-specific KNOWN_RATINGS hardcoded fallback table

    Returns {real_name: elo_rating}.
    """
    known = _KNOWN_RATINGS_BY_FORMAT.get(fmt, KNOWN_RATINGS)
    ratings = {}

    for name in players:
        rating = None

        # 1. Chess.com
        username = PLAYER_NAME_TO_USERNAME.get(name)
        if username:
            rating = get_player_rating(username, fmt=fmt)
            if rating:
                print(f"  {name}: {rating} (chess.com {fmt})")
            time.sleep(0.5)

        # 2. Hardcoded fallback
        if not rating:
            rating = known.get(name)
            if rating:
                print(f"  {name}: {rating} (hardcoded fallback)")

        if not rating:
            print(f"  WARNING: no rating found for '{name}', skipping")
            continue

        ratings[name] = rating

    return ratings


# ─── Schedule helper ──────────────────────────────────────────────────────────

def get_remaining_schedule(tournament_id: str,
                           completed_results: dict,
                           all_players: list,
                           total_rounds: int = None) -> list:
    """Returns all unplayed games for a round-robin tournament.

    Generates the full schedule (ordered pairs) and subtracts completed pairs.
    Names are normalised (strip + lower) before comparison so minor
    case/spacing differences between PGN names and the players list never
    cause a completed game to count as remaining.

    total_rounds is used to detect single vs double round-robin:
      - single RR: total_rounds == n-1   (e.g. Tata Steel: 13 rounds, 14 players)
      - double RR: total_rounds == 2*(n-1) (e.g. Candidates: 14 rounds, 8 players)
    In a single RR, completing game (A, B) also removes (B, A) from the schedule
    since each pair only plays once.
    """
    n = len(all_players)
    is_single_rr = (total_rounds is not None and total_rounds == n - 1)

    completed_pairs = set()
    for games in completed_results.values():
        for white, black, _ws, _bs in games:
            wl = white.strip().lower()
            bl = black.strip().lower()
            completed_pairs.add((wl, bl))
            if is_single_rr:
                # Each pair only plays once — reverse ordering also done
                completed_pairs.add((bl, wl))

    all_games = [(a, b) for a, b in permutations(all_players, 2)]

    # Debug: show sample pairs from each side so mismatches are visible
    print(f"  Sample completed pair: {list(completed_pairs)[:2]}")
    print(f"  Sample schedule pair:  {all_games[:2]}")
    print(f"  Format: {'single' if is_single_rr else 'double'} round-robin "
          f"({total_rounds} rounds, {n} players)")

    return [
        (w, b) for w, b in all_games
        if (w.strip().lower(), b.strip().lower()) not in completed_pairs
    ]
