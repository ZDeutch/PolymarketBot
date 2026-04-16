"""
PolymarketBot — Chess Tournament Simulator

Entry point for the tournament simulation pipeline.
Fetches live Lichess data, runs Monte Carlo simulation,
and compares model probabilities to Polymarket prices.

Usage:
  python main.py \\
    --tournament "FIDE Candidates 2026" \\
    --tour-id BLA70Vds \\
    --polymarket "2026-fide-candidates-tournament-winner"

Arguments:
  --tournament   Tournament display name (used in output header)
  --tour-id      8-char Lichess tour ID or slug.
                 Bypasses name-based slug resolution when provided.
                 e.g. BLA70Vds for FIDE Candidates 2026
  --polymarket   Polymarket event slug for this tournament
  --check        If passed, only show the edge table;
                 do not log positions
  --force        Override the pre-tournament gate (rounds > 4 check).
                 Use to run analysis mid-tournament for research only.

Gate:
  If more than ROUNDS_GATE rounds are complete the tool exits with a
  warning unless --force is supplied. This prevents stale edges from
  being acted on deep into a tournament where market prices have fully
  adjusted to known results.

Flow:
  1. Fetch tournament metadata from Lichess
  2. Fetch completed results and current FIDE ratings
  3. Run Monte Carlo simulation (50,000 iterations)
  4. Fetch Polymarket YES prices
  5. Calculate edges (model − market)
  6. Size positions with half-Kelly
"""

import argparse
import sys
import json
import requests
import time

from fetchers import chess_fetcher as fetcher
from simulators import chess_simulator as simulator
from edge_calculator import find_edges, size_positions
from config import SIMULATIONS, BANKROLL, MIN_EDGE

# ─── Polymarket slugs (FIDE Candidates 2026) ──────────────────────────────────
# Update this dict for each new tournament.
# Slug pattern: "will-{name-hyphenated}-win-the-{year}-{event}"

POLYMARKET_SLUGS = {
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
BASE_DELAY  = 2   # seconds

ROUNDS_GATE = 4   # exit if more rounds than this are complete (use --force to override)


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

            cr = requests.get("https://clob.polymarket.com/midpoint",
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


def get_market_prices() -> dict:
    """Fetches live Polymarket YES midpoint prices for all players in POLYMARKET_SLUGS."""
    prices = {}
    for player, slug in POLYMARKET_SLUGS.items():
        price = _fetch_clob_price(slug)
        if price is not None:
            prices[player] = price
            print(f"  {player}: {price:.3f}")
        else:
            print(f"  {player}: failed to fetch")
        time.sleep(0.5)
    return prices


# ─── Main run ─────────────────────────────────────────────────────────────────

def run(tournament_name: str,
        polymarket_slug: str,
        check_only: bool = False,
        tour_id: str | None = None,
        force: bool = False) -> None:
    """Full pipeline: fetch → simulate → edge table."""

    fetch_id = tour_id or tournament_name

    print("=" * 60)
    print(f"PolymarketBot — {tournament_name}")
    if tour_id:
        print(f"Tour ID: {tour_id}")
    print("=" * 60)

    # ── Step 1: Tournament metadata ───────────────────────────────────────────
    print(f"\nFetching tournament data...")
    tournament = fetcher.get_tournament(fetch_id)
    if not tournament:
        print("Failed to fetch tournament data.")
        sys.exit(1)

    rounds_complete = tournament.get("rounds_complete", 0)

    print(f"  Name:    {tournament.get('name', tournament_name)}")
    print(f"  Status:  {tournament.get('status', 'unknown')}")
    print(f"  Players: {len(tournament.get('players', []))}")
    print(f"  Rounds:  {rounds_complete}"
          f" / {tournament.get('total_rounds', '?')} complete")

    # ── Pre-tournament gate ───────────────────────────────────────────────────
    if rounds_complete > ROUNDS_GATE and not force:
        print(f"\n  ⚠ GATE: {rounds_complete} rounds complete "
              f"(threshold: {ROUNDS_GATE}).")
        print("  Markets have had time to price in results — edge estimates")
        print("  are unreliable this deep into the tournament.")
        print("  Pass --force to run anyway (research/analysis only).")
        sys.exit(0)

    if force and rounds_complete > ROUNDS_GATE:
        print(f"\n  --force: bypassing gate "
              f"({rounds_complete} rounds complete). Research mode.")

    # ── Step 2: Completed results ─────────────────────────────────────────────
    print(f"\nFetching completed results...")
    completed = fetcher.get_completed_results(fetch_id)

    # ── Step 3: Player ratings ────────────────────────────────────────────────
    print(f"\nFetching player ratings...")
    players = fetcher.get_players(fetch_id)
    print(f"  Players found: {len(players)}")
    ratings = fetcher.get_player_ratings(players)
    print(f"  Ratings fetched: {len(ratings)}/{len(players)}")

    if not ratings:
        print("ERROR: No ratings available. Cannot simulate.")
        sys.exit(1)

    # ── Step 4: Remaining schedule ────────────────────────────────────────────
    total_rounds = tournament.get("total_rounds")
    remaining = fetcher.get_remaining_schedule(
        fetch_id, completed, players, total_rounds
    )
    print(f"  Remaining games: {len(remaining)}")

    # ── Step 5: Monte Carlo simulation ───────────────────────────────────────
    print(f"\nRunning {SIMULATIONS:,} Monte Carlo simulations...")
    model_probs = simulator.simulate_tournament(
        ratings, completed, remaining, SIMULATIONS
    )

    # ── Step 6: Polymarket prices ─────────────────────────────────────────────
    print(f"\nFetching Polymarket prices...")
    market_prices = get_market_prices()
    if not market_prices:
        print("WARNING: No market prices — showing model-only output.")

    # ── Step 7: Edge table ────────────────────────────────────────────────────
    edges = find_edges(model_probs, market_prices)
    sized = size_positions(edges)

    print()
    if not market_prices:
        # Model-only output: no market prices available
        sep = "─" * 43
        print(sep)
        print(f"  {'Player':<23}  {'Model':>7}  {'Rank':>5}")
        print(sep)
        for rank, (player, prob) in enumerate(
            sorted(model_probs.items(), key=lambda x: x[1], reverse=True), 1
        ):
            if prob > 0.0001:
                print(f"  {player:<23}  {prob*100:>6.1f}%  #{rank}")
        print(sep)
    else:
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
        description="PolymarketBot — Chess Tournament Simulator"
    )
    parser.add_argument("--tournament", required=True,
                        help="Tournament display name (used in output header)")
    parser.add_argument("--tour-id", default=None,
                        dest="tour_id",
                        help="8-char Lichess tour ID or slug. "
                             "e.g. BLA70Vds for FIDE Candidates 2026")
    parser.add_argument("--polymarket", required=True,
                        help="Polymarket event slug")
    parser.add_argument("--check", action="store_true",
                        help="Show edge table only; do not log positions")
    parser.add_argument("--force", action="store_true",
                        help=f"Override the >{ROUNDS_GATE}-round gate; "
                             "run analysis mid-tournament for research")

    args = parser.parse_args()
    run(args.tournament, args.polymarket, args.check,
        tour_id=args.tour_id, force=args.force)
