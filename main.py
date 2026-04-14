"""
PolymarketBot — Multi-Sport Tournament Simulator

Entry point for the tournament simulation and
paper trading system. Supports chess, tennis,
and golf tournaments.

Usage:
  python main.py \\
    --tournament "FIDE Candidates 2026" \\
    --sport chess \\
    --polymarket "2026-fide-candidates-tournament-winner"

  python main.py \\
    --tournament "Wimbledon 2026" \\
    --sport tennis \\
    --polymarket "wimbledon-2026-mens-winner"

  python main.py \\
    --tournament "The Masters 2026" \\
    --sport golf \\
    --polymarket "2026-masters-winner"

Arguments:
  --tournament   Tournament name (exact, as known to
                 the sport's data source)
  --sport        One of: chess, tennis, golf
  --polymarket   Polymarket event slug for this
                 tournament
  --check        If passed, only show current P&L
                 without logging new positions

Flow:
  1. Detect sport and load correct fetcher/simulator
  2. Fetch completed results and current ratings
  3. Run Monte Carlo simulation
  4. Fetch Polymarket prices
  5. Calculate edges
  6. Size positions with half-Kelly
  7. Settle any open positions from previous run
  8. Log new positions to Google Sheets
"""

import argparse
import sys
import json
import requests
import time

from sport_detector import detect_sport, get_fetcher, get_simulator
from edge_calculator import find_edges, size_positions
from config import SIMULATIONS, BANKROLL, MIN_EDGE

# ─── Chess Polymarket slugs ────────────────────────────────────────────────────

POLYMARKET_SLUGS_CHESS = {
    "Fabiano Caruana":
        "will-fabiano-caruana-win-the-2026-fide-candidates-tournament",
    "Hikaru Nakamura":
        "will-hikaru-nakamura-win-the-2026-fide-candidates-tournament",
    "Javokhir Sindarov":
        "will-javokhir-sindarov-win-the-2026-fide-candidates-tournament",
    "Praggnanandhaa R":
        "will-praggnanandhaa-r-win-the-2026-fide-candidates-tournament",
    "Anish Giri":
        "will-anish-giri-win-the-2026-fide-candidates-tournament",
    "Wei Yi":
        "will-wei-yi-win-the-2026-fide-candidates-tournament",
    "Andrey Esipenko":
        "will-andrey-esipenko-win-the-2026-fide-candidates-tournament",
    "Matthias Bluebaum":
        "will-matthias-bluebaum-win-the-2026-fide-candidates-tournament",
}

MAX_RETRIES = 3
BASE_DELAY  = 2  # seconds


def _fetch_clob_price(slug: str) -> float | None:
    """Fetches the YES midpoint price for a Polymarket slug via Gamma → CLOB."""
    for attempt in range(MAX_RETRIES):
        try:
            url  = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
            data = requests.get(url, timeout=10).json()

            outcomes  = json.loads(data.get("outcomes",     "[]"))
            token_ids = json.loads(data.get("clobTokenIds", "[]"))
            yes_token = dict(zip(outcomes, token_ids)).get("Yes")
            if not yes_token:
                return None

            cr    = requests.get("https://clob.polymarket.com/midpoint",
                                 params={"token_id": yes_token}, timeout=10)
            return float(cr.json()["mid"])

        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = BASE_DELAY * (2 ** attempt)
                print(f"    Retry {attempt + 1} in {wait}s... ({e})")
                time.sleep(wait)
            else:
                return None
    return None


def get_market_prices(sport: str, polymarket_slug: str) -> dict:
    """Fetches live Polymarket YES midpoint prices for all players.

    For chess, uses POLYMARKET_SLUGS_CHESS per-player slugs.
    For other sports, uses polymarket_slug as the event slug (future use).
    """
    prices = {}

    if sport == "chess":
        for player, slug in POLYMARKET_SLUGS_CHESS.items():
            price = _fetch_clob_price(slug)
            if price is not None:
                prices[player] = price
                print(f"  {player}: {price:.3f}")
            else:
                print(f"  {player}: failed to fetch")
            time.sleep(0.5)

    return prices


# ─── Main run ─────────────────────────────────────────────────────────────────

def run(tournament_name: str, sport: str,
        polymarket_slug: str, check_only: bool = False) -> None:
    """Full pipeline: fetch → simulate → edge table → (optionally) log."""

    print("=" * 60)
    print(f"PolymarketBot — {tournament_name}")
    print(f"Sport: {sport.upper()}")
    print("=" * 60)

    fetcher   = get_fetcher(sport)
    simulator = get_simulator(sport)

    # ── Step 1: Tournament metadata ───────────────────────────────────────────
    print(f"\nFetching tournament data...")
    tournament = fetcher.get_tournament(tournament_name)
    if not tournament:
        print("Failed to fetch tournament data.")
        sys.exit(1)

    print(f"  Name:    {tournament.get('name', tournament_name)}")
    print(f"  Status:  {tournament.get('status', 'unknown')}")
    print(f"  Players: {len(tournament.get('players', []))}")
    print(f"  Rounds:  {tournament.get('rounds_complete', 0)}"
          f" / {tournament.get('total_rounds', '?')} complete")

    # ── Step 2: Completed results ─────────────────────────────────────────────
    print(f"\nFetching completed results...")
    completed = fetcher.get_completed_results(tournament_name)

    # ── Step 3: Player ratings ────────────────────────────────────────────────
    print(f"\nFetching player ratings...")
    players = tournament.get("players", [])
    ratings = fetcher.get_player_ratings(players)
    print(f"  Ratings fetched: {len(ratings)}/{len(players)}")

    if not ratings:
        print("ERROR: No ratings available. Cannot simulate.")
        sys.exit(1)

    # ── Step 4: Remaining schedule ────────────────────────────────────────────
    remaining = fetcher.get_remaining_schedule(tournament_name, completed, players)
    print(f"  Remaining games: {len(remaining)}")

    # ── Step 5: Monte Carlo simulation ───────────────────────────────────────
    print(f"\nRunning {SIMULATIONS:,} Monte Carlo simulations...")
    model_probs = simulator.simulate_tournament(
        ratings, completed, remaining, SIMULATIONS
    )

    # ── Step 6: Polymarket prices ─────────────────────────────────────────────
    print(f"\nFetching Polymarket prices...")
    market_prices = get_market_prices(sport, polymarket_slug)
    if not market_prices:
        print("WARNING: No market prices — showing model-only output.")

    # ── Step 7: Edge table ────────────────────────────────────────────────────
    edges = find_edges(model_probs, market_prices)
    sized = size_positions(edges)

    print()
    print("─" * 68)
    print(f"  {'Player':<23}  {'Model':>7}  {'Market':>7}  "
          f"{'Edge':>7}  {'Action':<8}  {'Stake':>8}")
    print("─" * 68)

    for e in sized:
        flag      = " ★" if e["action"] != "neutral" else ""
        stake_str = f"${e['stake']:>7,.0f}" if e["stake"] > 0 else "       —"
        sign      = "+" if e["edge"] >= 0 else ""
        print(f"  {e['player']:<23}  "
              f"{e['model']*100:>6.1f}%  "
              f"{e['market']*100:>6.1f}%  "
              f"{sign}{e['edge']*100:>5.1f}%  "
              f"{e['action']:<8}  "
              f"{stake_str}{flag}")

    print("─" * 68)

    actionable  = [e for e in sized if e["action"] != "neutral" and e["stake"] > 0]
    total_stake = sum(e["stake"] for e in actionable)

    print(f"\n  Actionable edges: {len(actionable)}  |  "
          f"Total stake: ${total_stake:,.2f}  "
          f"({total_stake / BANKROLL * 100:.1f}% of bankroll)")

    if check_only:
        print("\n  --check passed: no positions logged.")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PolymarketBot — Multi-Sport Tournament Simulator"
    )
    parser.add_argument("--tournament", required=True,
                        help="Tournament name or Chess.com tournament ID")
    parser.add_argument("--sport", required=True,
                        choices=["chess", "tennis", "golf"],
                        help="Sport type")
    parser.add_argument("--polymarket", required=True,
                        help="Polymarket event slug")
    parser.add_argument("--check", action="store_true",
                        help="Show edge table only; do not log positions")

    args = parser.parse_args()
    run(args.tournament, args.sport, args.polymarket, args.check)
