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
