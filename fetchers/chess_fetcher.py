"""
Fetches chess tournament data from the Chess.com
public API.

Chess.com API base URL: https://api.chess.com/pub

Key data contract — all functions return
standardized formats used by chess_simulator.py:

  get_tournament(tournament_id: str) -> dict
    Fetches tournament metadata including rounds,
    players, and current status.
    Returns: {
      "name": str,
      "players": [str],
      "total_rounds": int,
      "rounds_complete": int,
      "status": str
    }

  get_completed_results(tournament_id: str) -> dict
    Fetches all completed round results.
    Returns: {
      round_number: [
        (white: str, black: str,
         white_score: float, black_score: float),
        ...
      ]
    }

  get_player_ratings(players: list) -> dict
    Fetches current classical Elo rating for
    each player via Chess.com player stats endpoint.
    Falls back to FIDE scraping if Chess.com
    rating unavailable.
    Returns: {player_name: elo_rating}

  get_remaining_schedule(tournament_id: str) -> list
    Returns list of (white, black) tuples for
    all unplayed games in round order.

Note: Chess.com uses usernames not real names.
Requires a name→username lookup table for
major tournament players.
"""

import time
import requests
from itertools import permutations

BASE_URL = "https://api.chess.com/pub"
HEADERS  = {
    "User-Agent": "PolymarketBot/1.0 tournament-simulator"
}

# Maps real player names to Chess.com usernames
PLAYER_NAME_TO_USERNAME = {
    "Fabiano Caruana":    "fabianocaruana",
    "Hikaru Nakamura":    "hikaru",
    "Javokhir Sindarov":  "sindarov_j",
    "Praggnanandhaa R":   "rpraggnanandhaa",
    "Anish Giri":         "anishgiri",
    "Wei Yi":             "weiyi_chess",
    "Andrey Esipenko":    "andrey_esipenko",
    "Matthias Bluebaum":  "matthiasbluebaum",
    "Magnus Carlsen":     "magnuscarlsen",
    "Ian Nepomniachtchi": "lachessis",
    "Gukesh Dommaraju":   "gukeshdommaraju",
    "Alireza Firouzja":   "firouzja2003",
    "Vidit Gujrathi":     "viditchess",
    "Nijat Abasov":       "nijatabasov",
    "Ding Liren":         "dinglirenofficial",
}


def get_tournament(tournament_id: str) -> dict:
    """Fetches tournament metadata from Chess.com.

    Returns standardized dict with name, status, players,
    total_rounds, rounds_complete, and round URLs.
    Returns {} on error.
    """
    url = f"{BASE_URL}/tournament/{tournament_id}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  Error fetching tournament '{tournament_id}': {e}")
        return {}

    rounds      = data.get("rounds", [])
    total_rounds = data.get("settings", {}).get("total_rounds", len(rounds))

    # Count rounds that have finished
    rounds_complete = 0
    for round_url in rounds:
        try:
            r = requests.get(round_url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            round_data = r.json()
            if round_data.get("status") == "finished":
                rounds_complete += 1
        except Exception:
            pass

    players = [p.get("username", "") for p in data.get("players", [])]

    return {
        "name":            data.get("name", tournament_id),
        "status":          data.get("status", "unknown"),
        "players":         players,
        "total_rounds":    total_rounds,
        "rounds_complete": rounds_complete,
        "rounds":          rounds,
    }


def get_round_results(tournament_id: str, round_num: int) -> list:
    """Fetches all completed games from a specific round.

    Hits the round endpoint to get group URLs, then fetches each group
    for individual game results.

    Returns list of (white_username, black_username, white_score, black_score).
    """
    url = f"{BASE_URL}/tournament/{tournament_id}/{round_num}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        round_data = resp.json()
    except Exception as e:
        print(f"  Error fetching round {round_num}: {e}")
        return []

    results = []
    group_urls = round_data.get("groups", [])

    for group_num, group_url in enumerate(group_urls, start=1):
        try:
            gr = requests.get(group_url, headers=HEADERS, timeout=10)
            gr.raise_for_status()
            group_data = gr.json()
        except Exception as e:
            print(f"  Error fetching group {group_num} of round {round_num}: {e}")
            continue

        for game in group_data.get("games", []):
            white = game.get("white", {}).get("username", "")
            black = game.get("black", {}).get("username", "")
            if not white or not black:
                continue

            white_result = game.get("white", {}).get("result", "")

            if white_result == "win":
                ws, bs = 1.0, 0.0
            elif white_result == "lose":
                ws, bs = 0.0, 1.0
            else:
                # draw, stalemate, insufficient, repetition,
                # agreed, timeout, resigned — treat as draw
                ws, bs = 0.5, 0.5

            results.append((white, black, ws, bs))

    return results


def get_completed_results(tournament_id: str) -> dict:
    """Fetches all completed round results grouped by round number.

    Returns {round_number: [(white, black, ws, bs), ...]}
    """
    tournament = get_tournament(tournament_id)
    if not tournament:
        return {}

    rounds          = tournament.get("rounds", [])
    rounds_complete = tournament.get("rounds_complete", 0)
    completed       = {}
    total_games     = 0

    for round_num in range(1, rounds_complete + 1):
        games = get_round_results(tournament_id, round_num)
        if games:
            completed[round_num] = games
            total_games += len(games)

    print(f"  Fetched {len(completed)} completed rounds, {total_games} total games")
    return completed


def get_player_rating(username: str) -> int | None:
    """Fetches current Elo rating for a Chess.com username.

    Tries classical rating first, falls back to rapid.
    Returns None and prints a warning on failure.
    """
    url = f"{BASE_URL}/player/{username}/stats"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # Classical first
        classical = data.get("chess_classical", {})
        if classical:
            rating = classical.get("last", {}).get("rating")
            if rating:
                return int(rating)

        # Rapid fallback
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

    Looks up each name in PLAYER_NAME_TO_USERNAME to get the Chess.com
    username, then calls get_player_rating(). Adds a 0.5s delay between
    requests. Returns {real_name: elo_rating}.
    """
    ratings = {}
    for name in players:
        username = PLAYER_NAME_TO_USERNAME.get(name)
        if not username:
            print(f"  WARNING: no Chess.com username for '{name}', skipping")
            continue

        rating = get_player_rating(username)
        if rating is not None:
            ratings[name] = rating
            print(f"  {name}: {rating}")

        time.sleep(0.5)

    return ratings


def get_remaining_schedule(tournament_id: str,
                           completed_results: dict,
                           all_players: list) -> list:
    """Returns all unplayed games for a round-robin tournament.

    Generates the full double round-robin schedule (every ordered pair),
    subtracts completed games, returns remaining (white, black) pairs.
    """
    completed_pairs = set()
    for games in completed_results.values():
        for white, black, _ws, _bs in games:
            completed_pairs.add((white, black))

    full_schedule = [(a, b) for a, b in permutations(all_players, 2)]
    return [(w, b) for w, b in full_schedule if (w, b) not in completed_pairs]
