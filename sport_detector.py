"""
Detects the sport for a given tournament name string
and routes to the correct fetcher and simulator.

Supported sports: chess, tennis, golf

Detection logic uses keyword matching on the
tournament name. Examples:
  "FIDE Candidates 2026"    → chess
  "Wimbledon 2026"          → tennis
  "The Masters 2026"        → golf
  "US Open Tennis"          → tennis
  "US Open Golf"            → golf

Functions:
  detect_sport(tournament_name: str) -> str
    Returns one of: "chess", "tennis", "golf"
    Raises ValueError if sport cannot be determined.

  get_fetcher(sport: str) -> module
    Returns the appropriate fetcher module.

  get_simulator(sport: str) -> module
    Returns the appropriate simulator module.
"""

# ─── Keyword lists ────────────────────────────────────────────────────────────

CHESS_KEYWORDS = [
    "chess", "fide", "candidates", "olympiad",
    "grand prix", "world chess", "tata steel",
    "norway chess", "sinquefield",
]

TENNIS_KEYWORDS = [
    "tennis", "wimbledon", "us open tennis",
    "french open", "roland garros", "australian open",
    "atp", "wta", "masters tennis", "grand slam tennis",
]

GOLF_KEYWORDS = [
    "golf", "masters", "pga", "us open golf",
    "the open", "british open", "ryder cup",
    "tour championship", "fed ex",
]


# ─── Detection ────────────────────────────────────────────────────────────────

def detect_sport(tournament_name: str) -> str:
    """Returns 'chess', 'tennis', or 'golf' from a tournament name string.

    Chess is checked first (most specific). Tennis is checked before golf
    to correctly handle 'US Open Tennis' vs 'US Open Golf'.
    Raises ValueError if no sport can be determined.
    """
    name_lower = tournament_name.lower()

    # Chess first — most distinctive keywords
    if any(kw in name_lower for kw in CHESS_KEYWORDS):
        return "chess"

    # Tennis before golf — both share "US Open"
    if any(kw in name_lower for kw in TENNIS_KEYWORDS):
        return "tennis"

    if any(kw in name_lower for kw in GOLF_KEYWORDS):
        return "golf"

    raise ValueError(
        f"Cannot detect sport from: '{tournament_name}'. "
        f"Supported: chess, tennis, golf"
    )


# ─── Module routing ───────────────────────────────────────────────────────────

def get_fetcher(sport: str):
    """Returns the fetcher module for the given sport."""
    if sport == "chess":
        from fetchers import chess_fetcher
        return chess_fetcher
    elif sport == "tennis":
        from fetchers import tennis_fetcher
        return tennis_fetcher
    elif sport == "golf":
        from fetchers import golf_fetcher
        return golf_fetcher
    raise ValueError(f"Unknown sport: {sport}")


def get_simulator(sport: str):
    """Returns the simulator module for the given sport."""
    if sport == "chess":
        from simulators import chess_simulator
        return chess_simulator
    elif sport == "tennis":
        from simulators import tennis_simulator
        return tennis_simulator
    elif sport == "golf":
        from simulators import golf_simulator
        return golf_simulator
    raise ValueError(f"Unknown sport: {sport}")
