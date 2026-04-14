"""
Fetches tennis tournament data from the UTR
Sports API.

UTR API base URL: https://app.utrsports.net/api

Authentication: JWT token obtained by logging in
with UTR_EMAIL and UTR_PASSWORD from config.

Key data contract — all functions return
standardized formats used by tennis_simulator.py:

  authenticate() -> str
    Logs in to UTR API and returns JWT token.
    Token should be cached and reused within session.

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
