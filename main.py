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

# ─── Golf Polymarket slugs (Masters 2026) ─────────────────────────────────────
# Slug pattern: "will-{name-hyphenated}-win-the-2026-masters"
# Update event suffix when switching tournaments.

POLYMARKET_SLUGS_GOLF = {
    "Scottie Scheffler":  "will-scottie-scheffler-win-the-2026-masters",
    "Rory McIlroy":       "will-rory-mcilroy-win-the-2026-masters",
    "Xander Schauffele":  "will-xander-schauffele-win-the-2026-masters",
    "Collin Morikawa":    "will-collin-morikawa-win-the-2026-masters",
    "Ludvig Aberg":       "will-ludvig-aberg-win-the-2026-masters",
    "Jon Rahm":           "will-jon-rahm-win-the-2026-masters",
    "Bryson DeChambeau":  "will-bryson-dechambeau-win-the-2026-masters",
    "Tommy Fleetwood":    "will-tommy-fleetwood-win-the-2026-masters",
    "Viktor Hovland":     "will-viktor-hovland-win-the-2026-masters",
    "Patrick Cantlay":    "will-patrick-cantlay-win-the-2026-masters",
    "Shane Lowry":        "will-shane-lowry-win-the-2026-masters",
    "Hideki Matsuyama":   "will-hideki-matsuyama-win-the-2026-masters",
    "Justin Thomas":      "will-justin-thomas-win-the-2026-masters",
    "Jordan Spieth":      "will-jordan-spieth-win-the-2026-masters",
    "Brooks Koepka":      "will-brooks-koepka-win-the-2026-masters",
    "Max Homa":           "will-max-homa-win-the-2026-masters",
    "Cameron Young":      "will-cameron-young-win-the-2026-masters",
    "Akshay Bhatia":      "will-akshay-bhatia-win-the-2026-masters",
    "Tony Finau":         "will-tony-finau-win-the-2026-masters",
    "Jason Day":          "will-jason-day-win-the-2026-masters",
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

    Chess: uses POLYMARKET_SLUGS_CHESS per-player slugs.
    Golf:  uses POLYMARKET_SLUGS_GOLF per-player slugs.
    Other sports: reserved for future use.
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

    elif sport == "golf":
        for player, slug in POLYMARKET_SLUGS_GOLF.items():
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
        polymarket_slug: str, check_only: bool = False,
        tour_id: str | None = None) -> None:
    """Full pipeline: fetch → simulate → edge table → (optionally) log."""

    # For chess, --tour-id bypasses slug resolution (direct 8-char Lichess ID).
    # For golf/tennis, tour_id is always None and fetch_id == tournament_name.
    fetch_id = tour_id or tournament_name

    print("=" * 60)
    print(f"PolymarketBot — {tournament_name}")
    print(f"Sport: {sport.upper()}")
    if tour_id:
        print(f"Tour ID: {tour_id}")
    print("=" * 60)

    fetcher   = get_fetcher(sport)
    simulator = get_simulator(sport)

    # ── Step 1: Tournament metadata ───────────────────────────────────────────
    print(f"\nFetching tournament data...")
    tournament = fetcher.get_tournament(fetch_id)
    if not tournament:
        print("Failed to fetch tournament data.")
        sys.exit(1)

    print(f"  Name:    {tournament.get('name', tournament_name)}")
    print(f"  Status:  {tournament.get('status', 'unknown')}")
    print(f"  Players: {len(tournament.get('players', []))}")
    print(f"  Rounds:  {tournament.get('rounds_complete', 0)}"
          f" / {tournament.get('total_rounds', '?')} complete")

    if sport == "golf":
        # ── Golf pipeline (stroke play) ───────────────────────────────────────

        # ── Step 2: Live scores ───────────────────────────────────────────────
        print(f"\nFetching live scores...")
        scores = fetcher.get_live_scores(tournament_name)
        completed_rounds = fetcher.get_completed_rounds(scores)
        survivors = sum(1 for d in scores.values() if d.get("made_cut", True))
        print(f"  Players in field:   {len(scores)}")
        print(f"  Rounds complete:    {completed_rounds}/4")
        if completed_rounds >= 2:
            print(f"  Players made cut:   {survivors}/{len(scores)}")

        # ── Step 3: OWGR rankings ─────────────────────────────────────────────
        print(f"\nFetching OWGR rankings...")
        rankings = fetcher.get_owgr_rankings()
        print(f"  OWGR players loaded: {len(rankings)}")

        # Build field from tournament players × OWGR rankings.
        # Players not in OWGR top-list get a default rank of 100.
        field_players = tournament.get("players") or list(scores.keys())
        field = {p: rankings.get(p, 100) for p in field_players if p}
        ranked = sum(1 for v in field.values() if v <= len(rankings))
        print(f"  Field with OWGR data: {ranked}/{len(field)}")

        if not field:
            print("ERROR: Empty field — cannot simulate.")
            sys.exit(1)

        # ── Step 5: Monte Carlo simulation ───────────────────────────────────
        print(f"\nRunning {SIMULATIONS:,} Monte Carlo simulations...")
        model_probs = simulator.simulate_tournament(
            field, scores, completed_rounds, SIMULATIONS
        )

    elif sport == "tennis":
        # ── Tennis pipeline (bracket knockout) ───────────────────────────────

        # ── Step 2: Bracket (completed matches) ───────────────────────────────
        print(f"\nFetching bracket...")
        bracket = fetcher.get_bracket(tournament_name)
        rounds_done = max(bracket.keys()) if bracket else 0
        total_matches = sum(len(v) for v in bracket.values())
        print(f"  Rounds with results: {rounds_done}")
        print(f"  Completed matches:   {total_matches}")

        # ── Step 3: Players ───────────────────────────────────────────────────
        print(f"\nFetching players...")
        players = fetcher.get_players(tournament_name)
        print(f"  Players in field: {len(players)}")

        if not players:
            print("ERROR: No players found. Cannot simulate.")
            sys.exit(1)

        # ── Step 4: ATP rankings + ratings ────────────────────────────────────
        print(f"\nFetching ATP rankings...")
        rankings = fetcher.get_atp_rankings()
        ratings  = fetcher.get_player_ratings(players, rankings)
        ranked   = sum(1 for v in ratings.values() if v > 100)
        print(f"  Players with ATP data: {ranked}/{len(players)}")

        # ── Step 4b: Seeds ────────────────────────────────────────────────────
        print(f"\nFetching seeds...")
        seeds = fetcher.get_seeds(tournament_name)
        seeded = sum(1 for v in seeds.values() if v is not None)
        print(f"  Seeded players: {seeded}/{len(players)}")

        # ── Step 5: Monte Carlo simulation ───────────────────────────────────
        surface = tournament.get("surface", "hard")
        print(f"\nRunning {SIMULATIONS:,} Monte Carlo simulations "
              f"(surface: {surface})...")
        model_probs = simulator.simulate_tournament(
            players, ratings, seeds, bracket, surface, SIMULATIONS
        )

    else:
        # ── Chess pipeline ────────────────────────────────────────────────────

        # ── Step 2: Completed results ─────────────────────────────────────────
        print(f"\nFetching completed results...")
        completed = fetcher.get_completed_results(fetch_id)

        # ── Step 3: Player ratings ────────────────────────────────────────────
        print(f"\nFetching player ratings...")
        players = fetcher.get_players(fetch_id)
        print(f"  Players found: {len(players)}")
        ratings = fetcher.get_player_ratings(players)
        print(f"  Ratings fetched: {len(ratings)}/{len(players)}")

        if not ratings:
            print("ERROR: No ratings available. Cannot simulate.")
            sys.exit(1)

        # ── Step 4: Remaining schedule ────────────────────────────────────────
        total_rounds = tournament.get("total_rounds")
        remaining = fetcher.get_remaining_schedule(
            fetch_id, completed, players, total_rounds
        )
        print(f"  Remaining games: {len(remaining)}")

        # ── Step 5: Monte Carlo simulation ───────────────────────────────────
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
    col = 30 if sport == "golf" else 23   # golf names are longer
    if not market_prices:
        # Model-only output: no market prices available
        sep = "─" * (col + 20)
        print(sep)
        print(f"  {'Player':<{col}}  {'Model':>7}  {'Rank':>5}")
        print(sep)
        for rank, (player, prob) in enumerate(
            sorted(model_probs.items(), key=lambda x: x[1], reverse=True), 1
        ):
            if prob > 0.0001:
                print(f"  {player:<{col}}  {prob*100:>6.1f}%  #{rank}")
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
        description="PolymarketBot — Multi-Sport Tournament Simulator"
    )
    parser.add_argument("--tournament", required=True,
                        help="Lichess broadcast tour ID or slug "
                             "e.g. BLA70Vds or fide-candidates-2026-open")
    parser.add_argument("--sport", required=True,
                        choices=["chess", "tennis", "golf"],
                        help="Sport type")
    parser.add_argument("--polymarket", required=True,
                        help="Polymarket event slug")
    parser.add_argument("--check", action="store_true",
                        help="Show edge table only; do not log positions")
    parser.add_argument("--tour-id", default=None,
                        dest="tour_id",
                        help="Direct 8-char Lichess tour ID (chess only). "
                             "Bypasses slug resolution when provided. "
                             "e.g. BLA70Vds for FIDE Candidates 2026")

    args = parser.parse_args()
    run(args.tournament, args.sport, args.polymarket, args.check,
        tour_id=args.tour_id)
