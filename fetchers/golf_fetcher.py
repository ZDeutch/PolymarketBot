"""
Fetches golf tournament data from the DataGolf API.

DataGolf API base URL: https://feeds.datagolf.com
Authentication: API key passed as query parameter.

Key data contract — all functions return
standardized formats used by golf_simulator.py:

  get_field(tournament_id: str) -> dict
    Fetches current tournament field with
    player ratings and rankings.
    Returns: {
      player_name: {
        "owgr": int,
        "datagolf_rank": int,
        "baseline_avg": float,
        "scoring_avg": float
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
    Returns the number of fully completed rounds.

  get_model_predictions(tournament_id: str) -> dict
    Fetches DataGolf's own win probability model
    for comparison against our simulation.
    Returns: {player_name: win_probability}

Note: DataGolf free tier allows 100 API calls/day.
Cache responses locally to avoid hitting limits.
"""
