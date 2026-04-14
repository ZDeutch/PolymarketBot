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
