"""
Fetches tennis tournament data from the UTR Sports
unofficial API.

UTR API base URL: https://app.universaltennis.com/api

Authentication: JWT token from UTR user session.
Store as UTR_JWT_TOKEN in .env file.
Obtain by: logging into utrsports.net, opening
browser DevTools → Application → Cookies →
copy the value of the 'jwt' cookie.

No paid plan required — free UTR account sufficient.

Key data contract — all functions return
standardized formats used by tennis_simulator.py:

  get_tournament(tournament_id: str, token: str) -> dict
    Fetches tournament metadata including draw size,
    round structure, and player list.
    Returns: {
      "name": str,
      "players": [str],
      "format": "single_elimination" | "round_robin",
      "total_rounds": int,
      "rounds_complete": int
    }

  get_bracket(tournament_id: str, token: str) -> dict
    Fetches the full bracket structure including
    completed and pending matches.
    Returns: {
      round_number: [
        {
          "player_a": str,
          "player_b": str,
          "winner": str | None,
          "score": str | None,
          "position": int
        }
      ]
    }

  get_player_ratings(players: list,
                     token: str) -> dict
    Fetches current UTR rating for each player.
    Returns: {player_name: utr_rating}

Note: UTR ratings are on a 0-16.5 scale.
A 1-point UTR difference is meaningful at elite level.
"""

import requests
import csv
import io
import math
import time
from datetime import datetime

ESPN_BASE         = "https://site.api.espn.com/apis/site/v2/sports/tennis"
SACKMANN_RANKINGS = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_rankings_current.csv"
SACKMANN_PLAYERS  = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master/atp_players.csv"
HEADERS           = {"User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)"}

# Surface keywords (checked case-insensitively against tournament name)
_GRASS_KEYWORDS = ["wimbledon", "queen's", "queens", "halle", "nottingham",
                   "eastbourne", "s-hertogenbosch", "'s-hertogenbosch"]
_CLAY_KEYWORDS  = ["roland garros", "french open", "monte-carlo", "monte carlo",
                   "madrid", "rome", "barcelona", "hamburg", "rio",
                   "estoril", "lyon", "munich", "bucharest", "marrakech"]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _infer_surface(name: str) -> str:
    """Infers court surface from tournament name."""
    nl = name.lower()
    if any(k in nl for k in _GRASS_KEYWORDS):
        return "grass"
    if any(k in nl for k in _CLAY_KEYWORDS):
        return "clay"
    return "hard"


def _espn_scoreboard() -> dict:
    """Fetches the ESPN ATP tennis scoreboard JSON."""
    url = f"{ESPN_BASE}/atp/scoreboard"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _find_event(events: list, tournament_id: str) -> dict:
    """Returns the best-matching event for tournament_id.

    'current' → first in-progress event, else first event.
    Numeric ID → event with matching id, else first event.
    """
    if not events:
        return {}

    if tournament_id == "current":
        for e in events:
            state = e.get("status", {}).get("type", {}).get("state", "pre")
            if state == "in":
                return e
        return events[0]

    for e in events:
        if str(e.get("id", "")) == str(tournament_id):
            return e
    return events[0]


def _get_competitions_by_round(event: dict) -> dict:
    """Organises competitions from an ESPN tennis event into {round_num: [comps]}.

    ESPN tennis may structure data as:
      - event.groupings[i].competitions  (each grouping = one round)
      - event.competitions               (flat list, each has round.value)
    """
    by_round: dict[int, list] = {}

    # Try groupings first
    groupings = event.get("groupings", [])
    if groupings:
        for grp in groupings:
            r = grp.get("round", {}).get("value", 1)
            comps = grp.get("competitions", [])
            by_round.setdefault(r, []).extend(comps)
        return by_round

    # Flat competitions list
    for comp in event.get("competitions", []):
        r = comp.get("round", {}).get("value", 1)
        by_round.setdefault(r, []).append(comp)

    return by_round


def _parse_competition(comp: dict) -> dict | None:
    """Parses a single ESPN competition (match) into a normalised dict."""
    competitors = comp.get("competitors", [])
    if len(competitors) < 2:
        return None

    p_a = competitors[0].get("athlete", {}).get("displayName", "")
    p_b = competitors[1].get("athlete", {}).get("displayName", "")
    if not p_a or not p_b:
        return None
    if p_a in ("TBD", "BYE") or p_b in ("TBD", "BYE"):
        return None

    status_type = comp.get("status", {}).get("type", {})
    completed   = status_type.get("completed", False)

    winner = None
    if completed:
        w_a = competitors[0].get("winner", False)
        w_b = competitors[1].get("winner", False)
        if w_a:
            winner = p_a
        elif w_b:
            winner = p_b

    return {
        "player_a":  p_a,
        "player_b":  p_b,
        "winner":    winner,
        "completed": completed and winner is not None,
    }


# ─── ATP rankings (Sackmann GitHub) ───────────────────────────────────────────

def get_atp_rankings() -> dict:
    """Fetches current ATP rankings from the Sackmann GitHub repository.

    Joins atp_rankings_current.csv with atp_players.csv to produce:
      {full_name: {"rank": int, "points": int}}

    Uses only the most recent ranking week in the file.
    Prints a summary line and returns the joined dict.
    """
    # ── Step 1: rankings CSV ──────────────────────────────────────────────
    try:
        r = requests.get(SACKMANN_RANKINGS, headers=HEADERS, timeout=15)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        rows = list(reader)
    except Exception as e:
        print(f"  Sackmann rankings fetch failed: {e}")
        return {}

    # Keep only the most recent ranking_date
    dates = [row.get("ranking_date", "") for row in rows if row.get("ranking_date")]
    if not dates:
        print("  No ranking dates found in CSV.")
        return {}
    latest_date = max(dates)
    latest_rows = [row for row in rows if row.get("ranking_date") == latest_date]

    by_id: dict[str, dict] = {}
    for row in latest_rows:
        pid  = row.get("player", "").strip()
        rank = row.get("rank", "").strip()
        pts  = row.get("points", "").strip()
        if pid and rank:
            try:
                by_id[pid] = {
                    "rank":   int(rank),
                    "points": int(pts) if pts else 0,
                }
            except ValueError:
                pass

    # ── Step 2: players CSV ───────────────────────────────────────────────
    try:
        r = requests.get(SACKMANN_PLAYERS, headers=HEADERS, timeout=15)
        r.raise_for_status()
        reader = csv.DictReader(io.StringIO(r.text))
        id_to_name: dict[str, str] = {}
        for row in reader:
            pid   = row.get("player_id", "").strip()
            first = row.get("name_first", "").strip()
            last  = row.get("name_last", "").strip()
            if pid and (first or last):
                id_to_name[pid] = f"{first} {last}".strip()
    except Exception as e:
        print(f"  Sackmann players fetch failed: {e}")
        id_to_name = {}

    # ── Step 3: join ──────────────────────────────────────────────────────
    rankings: dict[str, dict] = {}
    for pid, data in by_id.items():
        full_name = id_to_name.get(pid)
        if full_name:
            rankings[full_name] = data

    try:
        date_obj = datetime.strptime(latest_date, "%Y%m%d")
        date_str = date_obj.strftime("%Y-%m-%d")
    except ValueError:
        date_str = latest_date

    print(f"  Fetched {len(rankings)} ATP rankings (as of {date_str})")
    return rankings


# ─── ESPN tournament functions ────────────────────────────────────────────────

def get_tournament(tournament_id: str) -> dict:
    """Fetches current tournament metadata from the ESPN ATP scoreboard.

    tournament_id may be 'current' (most active/recent event) or a
    numeric ESPN event ID string (e.g. '401656789').

    Infers surface from tournament name. Estimates rounds_complete
    from how many rounds have all matches finished.

    Returns standardised dict; returns {} on error.
    """
    try:
        data = _espn_scoreboard()
    except Exception as e:
        print(f"  ESPN tennis error: {e}")
        return {}

    events = data.get("events", [])
    if not events:
        return {}

    event = _find_event(events, tournament_id)
    if not event:
        return {}

    name        = event.get("name", "")
    surface     = _infer_surface(name)
    status_type = event.get("status", {}).get("type", {})
    state       = status_type.get("state", "pre")

    # Count completed rounds
    by_round = _get_competitions_by_round(event)
    rounds_complete = 0
    total_rounds    = max(by_round.keys()) if by_round else 7

    for r in sorted(by_round.keys()):
        comps = by_round[r]
        if comps and all(
            c.get("status", {}).get("type", {}).get("completed", False)
            for c in comps
        ):
            rounds_complete = r
        else:
            break   # stop at first incomplete round

    return {
        "name":            name,
        "id":              event.get("id", ""),
        "status":          state,
        "surface":         surface,
        "rounds_complete": rounds_complete,
        "total_rounds":    total_rounds,
        "players":         [],   # populated by get_players()
    }


def get_bracket(tournament_id: str) -> dict:
    """Returns all completed match results organised by round number.

    Only includes matches where status.type.completed == True and
    a winner can be determined.

    Returns {round_number: [{"player_a", "player_b", "winner", "completed"}]}.
    """
    try:
        data = _espn_scoreboard()
    except Exception as e:
        print(f"  ESPN tennis error: {e}")
        return {}

    events = data.get("events", [])
    if not events:
        return {}

    event    = _find_event(events, tournament_id)
    by_round = _get_competitions_by_round(event)

    bracket: dict[int, list] = {}
    for round_num, comps in sorted(by_round.items()):
        matches = []
        for comp in comps:
            m = _parse_competition(comp)
            if m and m["completed"]:
                matches.append(m)
        if matches:
            bracket[round_num] = matches

    return bracket


def get_players(tournament_id: str) -> list:
    """Returns sorted list of all players in the tournament field.

    Collects all named (non-TBD) competitors from every competition
    in the ESPN scoreboard, whether completed or not.
    """
    try:
        data = _espn_scoreboard()
    except Exception:
        return []

    events = data.get("events", [])
    if not events:
        return []

    event    = _find_event(events, tournament_id)
    by_round = _get_competitions_by_round(event)

    names: set[str] = set()
    for comps in by_round.values():
        for comp in comps:
            for c in comp.get("competitors", []):
                name = c.get("athlete", {}).get("displayName", "")
                if name and name not in ("TBD", "BYE", ""):
                    names.add(name)

    return sorted(names)


def get_player_ratings(players: list, rankings: dict) -> dict:
    """Matches ESPN display names to Sackmann ATP ranking points.

    Name matching strategy (in order of preference):
      1. Exact match
      2. Unique last-name match
      3. First-initial + last-name fuzzy match

    Returns {player_name: atp_points}.
    Players with no match get 100 points (fringe player fallback).
    """
    # Build last-name lookup from full rankings dict
    by_last: dict[str, list] = {}
    for full_name, data in rankings.items():
        last = full_name.split()[-1].lower() if full_name.split() else ""
        by_last.setdefault(last, []).append((full_name, data))

    ratings = {}
    for player in players:
        matched = False
        player_parts = player.split()
        player_last  = player_parts[-1].lower() if player_parts else ""

        # 1. Exact match
        if player in rankings:
            ratings[player] = rankings[player]["points"]
            matched = True

        # 2. Unique last-name match
        if not matched:
            candidates = by_last.get(player_last, [])
            if len(candidates) == 1:
                ratings[player] = candidates[0][1]["points"]
                matched = True

        # 3. First initial + last name ("J. Sinner" → "Jannik Sinner")
        if not matched and len(player_parts) >= 2:
            first_initial = player_parts[0].rstrip(".")
            for full_name, data in rankings.items():
                full_parts = full_name.split()
                if (len(full_parts) >= 2
                        and full_parts[-1].lower() == player_last
                        and full_parts[0].lower().startswith(first_initial.lower())):
                    ratings[player] = data["points"]
                    matched = True
                    break

        if not matched:
            ratings[player] = 100   # fringe player / not ranked

    return ratings


def get_seeds(tournament_id: str) -> dict:
    """Returns seedings for players in the tournament field.

    Attempts to read seed numbers from ESPN competitor data first.
    Falls back to deriving seeds from ATP ranking order
    (highest-ranked player = seed 1, etc.).

    Returns {player_name: seed_number | None}.
    Seeds 1-32 for 128-draw; seeds 1-16 for 64-draw.
    Unseeded players map to None.
    """
    players = get_players(tournament_id)
    if not players:
        return {}

    # Try ESPN competitor seed data
    try:
        data    = _espn_scoreboard()
        events  = data.get("events", [])
        event   = _find_event(events, tournament_id) if events else {}
        by_round = _get_competitions_by_round(event)

        espn_seeds: dict[str, int] = {}
        for comps in by_round.values():
            for comp in comps:
                for c in comp.get("competitors", []):
                    name = c.get("athlete", {}).get("displayName", "")
                    # Seed may live at several paths in ESPN data
                    seed = (
                        c.get("seed")
                        or c.get("athlete", {}).get("seed")
                        or c.get("order")
                    )
                    if name and seed:
                        try:
                            espn_seeds[name] = int(seed)
                        except (ValueError, TypeError):
                            pass

        if espn_seeds:
            max_seed = 32 if len(players) >= 65 else 16
            return {
                p: espn_seeds[p] if espn_seeds.get(p, 999) <= max_seed else None
                for p in players
            }
    except Exception:
        pass

    # Fallback: derive seeds from ATP ranking of field
    rankings = get_atp_rankings()
    if not rankings:
        return {p: None for p in players}

    field_ratings = get_player_ratings(players, rankings)
    sorted_players = sorted(players, key=lambda p: -field_ratings.get(p, 0))
    max_seed = 32 if len(players) >= 65 else 16

    return {
        p: (i + 1 if i < max_seed else None)
        for i, p in enumerate(sorted_players)
    }
