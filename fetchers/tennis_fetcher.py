"""
Fetches tennis tournament data from ESPN ATP scoreboard.

ESPN API base URL:
  https://site.api.espn.com/apis/site/v2/sports/tennis

No authentication required.

Player ratings use a hardcoded ATP rankings dict
(ATP_RANKINGS_2026) rather than a live data fetch.
Update the dict before each major tournament.

Key data contract — all functions return
standardised formats used by tennis_simulator.py:

  get_atp_rankings() -> dict
    Returns {full_name: {"rank": int, "points": int}}
    from the hardcoded ATP_RANKINGS_2026 dict.

  get_tournament(tournament_id: str) -> dict
    Fetches tournament metadata including draw size,
    round structure, and surface.
    Returns: {
      "name": str,
      "id": str,
      "status": "pre" | "in" | "post",
      "surface": "hard" | "clay" | "grass",
      "rounds_complete": int,
      "total_rounds": int,
      "players": []
    }

  get_bracket(tournament_id: str) -> dict
    Fetches completed match results.
    Returns: {
      round_number: [
        {
          "player_a": str,
          "player_b": str,
          "winner": str | None,
          "completed": bool
        }
      ]
    }

  get_players(tournament_id: str) -> list[str]
    All named (non-TBD) competitors in the field.

  get_player_ratings(players: list,
                     rankings=None) -> dict
    Maps player names to ATP points from
    ATP_RANKINGS_2026.
    Returns: {player_name: atp_points}

  get_seeds(tournament_id: str) -> dict
    Returns {player_name: seed_number | None}.
"""

import requests

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/tennis"
HEADERS   = {"User-Agent": "Mozilla/5.0 (compatible; PolymarketBot/1.0)"}

# Surface keywords (checked case-insensitively against tournament name)
_GRASS_KEYWORDS = ["wimbledon", "queen's", "queens", "halle", "nottingham",
                   "eastbourne", "s-hertogenbosch", "'s-hertogenbosch"]
_CLAY_KEYWORDS  = ["roland garros", "french open", "monte-carlo", "monte carlo",
                   "madrid", "rome", "barcelona", "hamburg", "rio",
                   "estoril", "lyon", "munich", "bucharest", "marrakech"]


# ─── Hardcoded ATP rankings ────────────────────────────────────────────────────

ATP_RANKINGS_2026 = {
    "Jannik Sinner": 13350,
    "Carlos Alcaraz": 13240,
    "Alexander Zverev": 5555,
    "Novak Djokovic": 4710,
    "Felix Auger-Aliassime": 4100,
    "Ben Shelton": 3900,
    "Alex de Minaur": 3895,
    "Taylor Fritz": 3870,
    "Lorenzo Musetti": 3625,
    "Daniil Medvedev": 3560,
    "Alexander Bublik": 3445,
    "Casper Ruud": 2625,
    "Jiri Lehecka": 2540,
    "Karen Khachanov": 2410,
    "Andrey Rublev": 2350,
    "Flavio Cobolli": 2320,
    "Valentin Vacherot": 2168,
    "Tommy Paul": 2065,
    "Francisco Cerundolo": 2020,
    "Frances Tiafoe": 1965,
    "Luciano Darderi": 1920,
    "Learner Tien": 1885,
    "Alejandro Davidovich Fokina": 1870,
    "Cameron Norrie": 1778,
    "Jakub Mensik": 1700,
    "Arthur Rinderknech": 1676,
    "Holger Rune": 1620,
    "Jack Draper": 1610,
    "Tomas Martin Etcheverry": 1590,
    "Arthur Fils": 1440,
    "Corentin Moutet": 1433,
    "Tallon Griekspoor": 1430,
    "Brandon Nakashima": 1385,
    "Ugo Humbert": 1320,
    "Joao Fonseca": 1315,
    "Alex Michelsen": 1200,
    "Gabriel Diallo": 1175,
    "Jaume Munar": 1175,
    "Denis Shapovalov": 1120,
    "Zizou Bergs": 1110,
    "Terence Atmane": 1108,
    "Fabian Marozsan": 1105,
    "Sebastian Korda": 1100,
    "Mariano Navone": 1085,
    "Alejandro Tabilo": 1068,
    "Adrian Mannarino": 1025,
    "Tomas Machac": 980,
    "Marin Cilic": 950,
    "Botic Van De Zandschulp": 931,
    "Ethan Quinn": 927,
    "Yannick Hanfmann": 899,
    "Nuno Borges": 895,
    "Giovanni Mpetshi Perricard": 890,
    "Marton Fucsovics": 887,
    "Rafael Jodar": 886,
    "Daniel Altmaier": 880,
    "Sebastian Baez": 880,
    "Miomir Kecmanovic": 875,
    "Alexei Popyrin": 870,
    "Ignacio Buse": 864,
    "Roman Andres Burruchaga": 860,
    "Jenson Brooksby": 852,
    "Hubert Hurkacz": 845,
    "Camilo Ugo Carabelli": 825,
    "Raphael Collignon": 818,
    "Lorenzo Sonego": 810,
    "Stefanos Tsitsipas": 805,
    "Reilly Opelka": 803,
    "Juan Manuel Cerundolo": 803,
    "Arthur Cazaux": 777,
    "Alexander Blockx": 772,
    "Kamil Majchrzak": 767,
    "Thiago Agustin Tirante": 765,
    "Alexander Shevchenko": 751,
    "Marcos Giron": 750,
    "Valentin Royer": 742,
    "Vit Kopriva": 738,
    "Mattia Bellucci": 734,
    "Marco Trungelliti": 732,
    "James Duckworth": 723,
    "Jan-Lennard Struff": 709,
    "Damir Dzumhur": 694,
    "Cristian Garin": 679,
    "Zachary Svajda": 674,
    "Eliot Spizzirri": 674,
    "Sebastian Ofner": 673,
    "Dino Prizmic": 670,
    "Hamad Medjedovic": 664,
    "Aleksandar Vukic": 663,
    "Quentin Halys": 650,
    "Matteo Berrettini": 650,
    "Francisco Comesana": 649,
    "Roberto Bautista Agut": 649,
    "Pablo Carreno Busta": 646,
    "Alexandre Muller": 641,
    "Patrick Kypson": 640,
    "Jacob Fearnley": 639,
    "Aleksandar Kovacevic": 636,
    "Luca Van Assche": 635,
    "Wu Yibing": 634,
    "Rinky Hijikata": 632,
    "Matteo Arnaldi": 621,
    "Benjamin Bonzi": 616,
    "Stan Wawrinka": 608,
    "Jesper de Jong": 600,
    "Zhang Zhizhen": 595,
}
# Last updated: April 15, 2026
# Update before each major tournament


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


# ─── ATP rankings ─────────────────────────────────────────────────────────────

def get_atp_rankings() -> dict:
    """Returns ATP rankings from the hardcoded ATP_RANKINGS_2026 dict.

    Returns {full_name: {"rank": int, "points": int}}.
    Rank is determined by insertion order of ATP_RANKINGS_2026
    (rank 1 = first entry, rank 2 = second, etc.).
    """
    result = {}
    for rank, (name, pts) in enumerate(
        ATP_RANKINGS_2026.items(), start=1
    ):
        result[name] = {"rank": rank, "points": pts}
    print(f"  Using hardcoded ATP rankings "
          f"(April 2026, {len(result)} players)")
    return result


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


def get_player_ratings(players: list, rankings=None) -> dict:
    """Maps player names to ATP points from ATP_RANKINGS_2026.

    Name matching strategy (in order of preference):
      1. Exact match against ATP_RANKINGS_2026
      2. Last-name match (first entry with matching surname wins)

    The rankings parameter is accepted but ignored — ratings always
    come from the hardcoded ATP_RANKINGS_2026 dict.

    Returns {player_name: atp_points}.
    Players with no match get 100 points (fringe player fallback).
    """
    ratings = {}
    for name in players:
        points = ATP_RANKINGS_2026.get(name)
        if not points:
            last = name.split()[-1].lower()
            for full, pts in ATP_RANKINGS_2026.items():
                if full.split()[-1].lower() == last:
                    points = pts
                    break
        ratings[name] = points if points else 100
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

    # Fallback: derive seeds from ATP ranking points of field
    field_ratings  = get_player_ratings(players)
    sorted_players = sorted(players, key=lambda p: -field_ratings.get(p, 0))
    max_seed = 32 if len(players) >= 65 else 16

    return {
        p: (i + 1 if i < max_seed else None)
        for i, p in enumerate(sorted_players)
    }
