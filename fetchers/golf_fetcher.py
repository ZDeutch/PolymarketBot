"""
Fetches golf tournament data from free public sources.

Data sources:
  - PGA Tour internal JSON API (no auth required)
    Base: https://statdata.pgatour.com/r/{tournament_id}/
    Endpoints:
      leaderboard-v2.json  — live scores and standings
      field.json           — tournament field/players

  - OWGR website scraping (no auth required)
    URL: https://www.owgr.com/ranking
    Provides: Official World Golf Rankings for all players

Key data contract:

  get_field(tournament_id: str) -> dict
    Fetches current tournament field.
    Returns: {
      player_name: {
        "owgr": int,
        "country": str,
      }
    }

  get_live_scores(tournament_id: str) -> dict
    Fetches live/completed scoring for all rounds.
    Returns: {
      player_name: {
        "round_1": int | None,
        "round_2": int | None,
        "round_3": int | None,
        "round_4": int | None,
        "total": int | None,
        "position": int | None,
        "made_cut": bool
      }
    }

  get_completed_rounds(tournament_id: str) -> int
    Returns number of fully completed rounds (0-4).

  get_owgr_rankings() -> dict
    Scrapes current OWGR rankings.
    Returns: {player_name: owgr_rank}

Note: PGA Tour tournament IDs are numeric strings
like "034" for RBC Heritage. Find them in the
PGA Tour schedule URL structure.
"""

import requests
import json
import time
import re
from bs4 import BeautifulSoup

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/golf"
OWGR_URL  = "https://www.owgr.com/ranking"
HEADERS   = {
    "User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)"
}

# ─── OWGR fallback (top 50) ────────────────────────────────────────────────────
# Last updated: April 2026
# Source: OWGR week of Masters 2026
# Update monthly or before major tournaments

FALLBACK_OWGR = {
    "Scottie Scheffler":  1,
    "Rory McIlroy":       2,
    "Tommy Fleetwood":    3,
    "Xander Schauffele":  4,
    "Russell Henley":     5,
    "JJ Spaun":           6,
    "Robert MacIntyre":   7,
    "Ben Griffin":        8,
    "Justin Thomas":      9,
    "Justin Rose":       10,
    "Collin Morikawa":   11,
    "Ludvig Aberg":      12,
    "Jon Rahm":          13,
    "Bryson DeChambeau": 14,
    "Viktor Hovland":    15,
    "Patrick Cantlay":   16,
    "Shane Lowry":       17,
    "Wyndham Clark":     18,
    "Hideki Matsuyama":  19,
    "Sungjae Im":        20,
    "Tony Finau":        21,
    "Matt Fitzpatrick":  22,
    "Adam Scott":        23,
    "Keegan Bradley":    24,
    "Min Woo Lee":       25,
    "Tom Kim":           26,
    "Sahith Theegala":   27,
    "Max Homa":          29,
    "Cameron Young":     30,
    "Akshay Bhatia":     31,
    "Nick Dunlap":       32,
    "Harris English":    33,
    "Corey Conners":     34,
    "Sepp Straka":       35,
    "Jason Day":         36,
    "Sam Burns":         37,
    "Aaron Rai":         38,
    "Jordan Spieth":     39,
    "Brooks Koepka":     40,
    "Si Woo Kim":        41,
    "Stephan Jaeger":    42,
    "Nico Echavarria":   43,
    "Jake Knapp":        44,
    "Will Zalatoris":    45,
    "Emiliano Grillo":   46,
    "Bud Cauley":        47,
    "Davis Thompson":    48,
    "Byeong Hun An":     49,
    "Denny McCarthy":    50,
}


# ─── ESPN scoreboard (shared fetch) ───────────────────────────────────────────

def _espn_scoreboard() -> dict:
    """Fetches the current ESPN golf PGA scoreboard JSON."""
    url = f"{ESPN_BASE}/pga/scoreboard"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ─── Public functions ──────────────────────────────────────────────────────────

def get_tournament(tournament_id: str) -> dict:
    """Fetches current tournament metadata from the ESPN scoreboard.

    tournament_id may be 'current' (most recent/active tournament) or
    a numeric ESPN event ID string (e.g. '401353230' for Masters 2026).
    The ESPN scoreboard always returns the active/most-recent event, so
    the ID is accepted but the current scoreboard is always fetched.

    Returns standardised dict; returns {} on error.
    """
    try:
        data = _espn_scoreboard()
    except Exception as e:
        print(f"  Error fetching ESPN scoreboard: {e}")
        return {}

    events = data.get("events", [])
    if not events:
        print("  No events found in ESPN scoreboard response.")
        return {}

    event = events[0]

    status      = event.get("status", {})
    period      = status.get("period", 0)          # current round number
    status_type = status.get("type", {})
    state       = status_type.get("state", "pre")  # "pre" | "in" | "post"

    # Estimate rounds complete from ESPN period field
    if state == "post":
        rounds_complete = 4
    elif state == "in":
        rounds_complete = max(0, period - 1)
    else:
        rounds_complete = 0

    # Extract player list from competitors
    competitors = (
        event.get("competitions", [{}])[0].get("competitors", [])
    )
    players = []
    for c in competitors:
        name = c.get("athlete", {}).get("displayName", "")
        if name:
            players.append(name)

    return {
        "name":            event.get("name", ""),
        "id":              event.get("id", ""),
        "status":          state,
        "rounds_complete": rounds_complete,
        "total_rounds":    4,
        "players":         players,
    }


def get_live_scores(tournament_id: str) -> dict:
    """Fetches live or final round-by-round scores for all players.

    Parses the ESPN scoreboard competitors array.
    Round scores are raw stroke counts (e.g. 67, 70).
    Status id "6" = missed cut.

    Returns {player_name: {round_1..4, total, position, made_cut, status}}.
    """
    try:
        data = _espn_scoreboard()
    except Exception as e:
        print(f"  Error fetching ESPN scoreboard: {e}")
        return {}

    events = data.get("events", [])
    if not events:
        return {}

    event       = events[0]
    competitors = (
        event.get("competitions", [{}])[0].get("competitors", [])
    )

    scores = {}
    for comp in competitors:
        name = comp.get("athlete", {}).get("displayName", "")
        if not name:
            continue

        status_type = comp.get("status", {}).get("type", {})
        status_id   = status_type.get("id", "1")
        #  "1"=scheduled, "2"=active, "3"=complete, "6"=cut
        made_cut = (status_id != "6")

        # Position
        pos_obj = comp.get("status", {}).get("position", {})
        pos_str = pos_obj.get("displayName", "") if isinstance(pos_obj, dict) else ""
        try:
            position = int(pos_str)
        except (ValueError, TypeError):
            position = None

        # Round scores — linescores[i] = round i+1
        linescores   = comp.get("linescores", [])
        round_scores = {f"round_{i}": None for i in range(1, 5)}

        for i, ls in enumerate(linescores[:4], 1):
            val = ls.get("value", "")
            if val and val not in ("*", "--", ""):
                try:
                    round_scores[f"round_{i}"] = int(val)
                except (ValueError, TypeError):
                    pass

        completed_vals = [
            round_scores[f"round_{i}"]
            for i in range(1, 5)
            if round_scores[f"round_{i}"] is not None
        ]
        total = sum(completed_vals) if completed_vals else None

        scores[name] = {
            "round_1":  round_scores["round_1"],
            "round_2":  round_scores["round_2"],
            "round_3":  round_scores["round_3"],
            "round_4":  round_scores["round_4"],
            "total":    total,
            "position": position,
            "made_cut": made_cut,
            "status":   status_id,
        }

    return scores


def get_completed_rounds(scores: dict) -> int:
    """Determines how many rounds are fully complete from a scores dict.

    A round is considered complete if every player who made the cut
    has a non-None score for that round. For rounds 1-2 (pre-cut),
    all players are checked; for rounds 3-4, only survivors are checked.

    Returns int 0-4.
    """
    if not scores:
        return 0

    all_players = list(scores.keys())
    survivors   = [p for p, d in scores.items() if d.get("made_cut", True)]
    if not survivors:
        survivors = all_players

    for round_num in range(4, 0, -1):
        key   = f"round_{round_num}"
        check = survivors if round_num >= 3 else all_players
        if check and all(scores.get(p, {}).get(key) is not None for p in check):
            return round_num

    return 0


def get_owgr_rankings() -> dict:
    """Scrapes current OWGR rankings from owgr.com/ranking.

    Parses the HTML rankings table. Falls back to FALLBACK_OWGR (top 50)
    if scraping fails or returns fewer than 10 entries.

    Returns {player_name: owgr_rank}.
    """
    try:
        resp = requests.get(OWGR_URL, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        rankings = {}

        # Try to find the rankings table (by id or first table on page)
        table = (
            soup.find("table", {"id": re.compile(r"ranking", re.I)})
            or soup.find("table")
        )
        if not table:
            raise ValueError("No table found in OWGR page")

        for row in table.find_all("tr")[1:]:   # skip header row
            cells = row.find_all(["td", "th"])
            if len(cells) < 2:
                continue

            rank_raw = cells[0].get_text(strip=True)
            name_raw = cells[1].get_text(strip=True)

            # Strip non-digit chars from rank (e.g. "=", "T")
            rank_digits = re.sub(r"[^0-9]", "", rank_raw)
            if not rank_digits or not name_raw:
                continue

            try:
                rankings[name_raw] = int(rank_digits)
            except (ValueError, TypeError):
                continue

        if len(rankings) >= 10:
            print(f"  OWGR scraped: {len(rankings)} players")
            return rankings

        raise ValueError(f"Only {len(rankings)} rows scraped — too few")

    except Exception as e:
        print(f"  OWGR scrape failed ({e}), using fallback top-50")
        return dict(FALLBACK_OWGR)
