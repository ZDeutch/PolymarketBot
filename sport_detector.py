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
