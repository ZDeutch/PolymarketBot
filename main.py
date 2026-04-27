"""
PolymarketBot — Chess Tournament Simulator

Entry point for the tournament simulation pipeline.
Fetches live Lichess data, runs Monte Carlo simulation,
and compares model probabilities to Polymarket or Kalshi prices.

Usage:
  # Pre-tournament (no Lichess broadcast yet):
  python main.py \\
    --tournament "Norway Chess 2026" \\
    --no-broadcast --total-rounds 10 \\
    --exchange kalshi

  # Mid-tournament (Lichess broadcast live):
  python main.py \\
    --tournament "TePe Sigeman 2026" \\
    --tour-id XXXXXXXX \\
    --exchange polymarket \\
    --polymarket "tepe-sigeman-2026"

Arguments:
  --tournament     Tournament display name (used in output header and CSV)
  --tour-id        8-char Lichess tour ID. Required unless --no-broadcast.
  --exchange       Price source: polymarket (default) or kalshi
  --polymarket     Polymarket event slug (required when --exchange polymarket)
  --no-broadcast   Skip Lichess fetch entirely; use hardcoded player list.
                   Use before the Lichess broadcast is set up.
  --total-rounds   Total rounds in the tournament (used with --no-broadcast
                   to detect single vs double round-robin).
  --format         Time control: classical (default), rapid, or blitz.
  --check          Show edge table only; do not write to positions.csv.
  --force          Override the >4-round gate for research runs.

Gate:
  If more than ROUNDS_GATE rounds are complete the tool exits with a
  warning unless --force is supplied.

Flow:
  1. Fetch tournament metadata from Lichess (or use synthetic pre-tournament data)
  2. Fetch completed results and current FIDE ratings
  3. Run Monte Carlo simulation (50,000 iterations)
  4. Fetch market prices (Polymarket CLOB or Kalshi hardcoded)
  5. Calculate edges (model − market)
  6. Size positions with quarter-Kelly
  7. Print edge table; append actionable bets to positions.csv
"""

import argparse
import csv
import os
import sys
import json
import requests
import time
from datetime import date

from fetchers import chess_fetcher as fetcher
from simulators import chess_simulator as simulator
from edge_calculator import find_edges, size_positions
from config import SIMULATIONS, BANKROLL, MIN_EDGE

# ─── Current tournament: GCT Poland 2026 — Super Rapid & Blitz ────────────────
# Update POLYMARKET_SLUGS and KALSHI_PRICES for each new tournament.
# POLYMARKET_SLUGS keys are also used as the pre-tournament player list fallback.

POLYMARKET_SLUGS = {
    # GCT Poland 2026 Super Rapid & Blitz — Kalshi is the active exchange
    "Alireza Firouzja":           "TODO",
    "Jan-Krzysztof Duda":         "TODO",
    "Javokhir Sindarov":          "TODO",
    "Fabiano Caruana":            "TODO",
    "Maxime Vachier-Lagrave":     "TODO",
    "Wesley So":                  "TODO",
    "Gukesh Dommaraju":           "TODO",
    "Hans Niemann":               "TODO",
    "Radoslaw Wojtaszek":         "TODO",
    "Vladimir Fedoseev":          "TODO",
}

# Kalshi YES prices — GCT Poland 2026 Super Rapid & Blitz
# Last updated: April 27, 2026 from Kalshi screenshot (Chance column midpoints).
# URL: kalshi.com/markets/kxchesspoland/grand-chess-tour-super-rapid--blitz-poland/kxchesspoland-26
# Update these before each run using live prices from the Kalshi market page.
KALSHI_PRICES = {
    "Alireza Firouzja":           0.48,
    "Jan-Krzysztof Duda":         0.19,
    "Javokhir Sindarov":          0.19,
    "Fabiano Caruana":            0.14,
    "Maxime Vachier-Lagrave":     0.05,
    "Wesley So":                  0.05,
    "Gukesh Dommaraju":           0.03,
    "Hans Niemann":               0.03,
    "Radoslaw Wojtaszek":         0.03,
    "Vladimir Fedoseev":          0.03,
}

MAX_RETRIES = 3
BASE_DELAY  = 2   # seconds

ROUNDS_GATE = 4   # exit if more rounds than this are complete (use --force to override)

CSV_FILE    = "positions.csv"
CSV_HEADERS = ["Date", "Tournament", "Exchange", "Format",
               "Player", "Model%", "Market%", "Edge%", "Action", "Stake"]


# ─── Price fetching ────────────────────────────────────────────────────────────

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


def get_polymarket_prices() -> dict:
    """Fetches live Polymarket YES midpoint prices for all players in POLYMARKET_SLUGS."""
    prices = {}
    for player, slug in POLYMARKET_SLUGS.items():
        if slug == "TODO":
            print(f"  {player}: no Polymarket slug configured")
            continue
        price = _fetch_clob_price(slug)
        if price is not None:
            prices[player] = price
            print(f"  {player}: {price:.3f}")
        else:
            print(f"  {player}: failed to fetch")
        time.sleep(0.5)
    return prices


def get_kalshi_prices() -> dict:
    """Returns hardcoded Kalshi YES prices from KALSHI_PRICES.

    Update KALSHI_PRICES at the top of this file before each run
    using the live prices shown on the Kalshi market page.
    """
    print("  Source: KALSHI_PRICES (hardcoded — update before each run)")
    for player, price in KALSHI_PRICES.items():
        print(f"  {player}: {price:.3f}")
    return dict(KALSHI_PRICES)


def get_market_prices(exchange: str) -> dict:
    """Dispatches to the appropriate price source."""
    if exchange == "kalshi":
        return get_kalshi_prices()
    return get_polymarket_prices()


# ─── CSV logging ──────────────────────────────────────────────────────────────

def save_to_csv(sized: list,
                tournament_name: str,
                exchange: str,
                fmt: str) -> str:
    """Appends actionable positions to positions.csv.

    Creates the file with headers if it doesn't exist yet.
    Only writes rows where action != neutral and stake > 0.
    Returns the filename written to.
    """
    new_file = not os.path.exists(CSV_FILE)
    today    = date.today().isoformat()

    with open(CSV_FILE, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(CSV_HEADERS)
        for e in sized:
            if e["action"] == "neutral" or e["stake"] <= 0:
                continue
            w.writerow([
                today,
                tournament_name,
                exchange.capitalize(),
                fmt,
                e["player"],
                f"{e['model'] * 100:.1f}",
                f"{e['market'] * 100:.1f}",
                f"{e['edge'] * 100:.1f}",
                e["action"],
                f"{e['stake']:.2f}",
            ])

    return CSV_FILE


# ─── Main run ─────────────────────────────────────────────────────────────────

def run(tournament_name: str,
        polymarket_slug: str | None = None,
        check_only: bool = False,
        tour_id: str | None = None,
        force: bool = False,
        fmt: str = "classical",
        exchange: str = "polymarket",
        no_broadcast: bool = False,
        total_rounds_hint: int | None = None) -> None:
    """Full pipeline: fetch → simulate → edge table → CSV log."""

    print("=" * 60)
    print(f"PolymarketBot — {tournament_name}")
    if tour_id:
        print(f"Tour ID:  {tour_id}")
    print(f"Format:   {fmt}  |  Exchange: {exchange}")
    print("=" * 60)

    # ── Step 1: Tournament metadata ───────────────────────────────────────────
    tournament = {}
    if no_broadcast:
        # Pre-tournament: Lichess broadcast not yet set up.
        # Build synthetic metadata so the pipeline can proceed.
        tournament = {
            "name":            tournament_name,
            "total_rounds":    total_rounds_hint,
            "rounds_complete": 0,
            "rounds":          [],
            "players":         [],
        }
        print(f"\nPre-tournament mode (--no-broadcast).")
        print(f"  Total rounds: {total_rounds_hint or 'unknown'}")
    else:
        fetch_id = tour_id or tournament_name
        print(f"\nFetching tournament data...")
        tournament = fetcher.get_tournament(fetch_id)
        if not tournament:
            print("Failed to fetch tournament data.")
            sys.exit(1)

    rounds_complete = tournament.get("rounds_complete", 0)

    if not no_broadcast:
        print(f"  Name:    {tournament.get('name', tournament_name)}")
        print(f"  Status:  {tournament.get('status', 'unknown')}")
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
    if no_broadcast:
        completed = {}
        print(f"  No completed results (pre-tournament).")
    else:
        print(f"\nFetching completed results...")
        completed = fetcher.get_completed_results(tour_id or tournament_name)

    # ── Step 3: Player list ───────────────────────────────────────────────────
    print(f"\nFetching player list...")
    if no_broadcast:
        players = sorted(POLYMARKET_SLUGS.keys())
        print(f"  Using POLYMARKET_SLUGS player list ({len(players)} players)")
    else:
        players = fetcher.get_players(tour_id or tournament_name)
        if not players:
            players = sorted(POLYMARKET_SLUGS.keys())
            print(f"  No Lichess round 1 data yet — "
                  f"using POLYMARKET_SLUGS ({len(players)} players)")
        else:
            print(f"  Players found: {len(players)}")

    # ── Step 4a: Player ratings ───────────────────────────────────────────────
    print(f"\nFetching player ratings ({fmt})...")
    ratings = fetcher.get_player_ratings(players, fmt=fmt)
    print(f"  Ratings fetched: {len(ratings)}/{len(players)}")

    if not ratings:
        print("ERROR: No ratings available. Cannot simulate.")
        sys.exit(1)

    # ── Step 4b: Remaining schedule ───────────────────────────────────────────
    total_rounds = tournament.get("total_rounds") or total_rounds_hint
    remaining = fetcher.get_remaining_schedule(
        tour_id or tournament_name if not no_broadcast else "__pre__",
        completed, players, total_rounds
    )
    print(f"  Remaining games: {len(remaining)}")

    # ── Step 5: Monte Carlo simulation ───────────────────────────────────────
    print(f"\nRunning {SIMULATIONS:,} Monte Carlo simulations...")
    model_probs = simulator.simulate_tournament(
        ratings, completed, remaining, SIMULATIONS
    )

    # ── Step 6: Market prices ─────────────────────────────────────────────────
    print(f"\nFetching {exchange.capitalize()} prices...")
    market_prices = get_market_prices(exchange)
    if not market_prices:
        print("WARNING: No market prices — showing model-only output.")

    # ── Step 7: Edge table ────────────────────────────────────────────────────
    edges = find_edges(model_probs, market_prices)
    sized = size_positions(edges)

    print()
    if not market_prices:
        sep = "─" * 43
        print(sep)
        print(f"  {'Player':<28}  {'Model':>7}  {'Rank':>5}")
        print(sep)
        for rank, (player, prob) in enumerate(
            sorted(model_probs.items(), key=lambda x: x[1], reverse=True), 1
        ):
            if prob > 0.0001:
                print(f"  {player:<28}  {prob*100:>6.1f}%  #{rank}")
        print(sep)
    else:
        print("─" * 72)
        print(f"  {'Player':<28}  {'Model':>7}  {'Market':>7}  "
              f"{'Edge':>7}  {'Action':<8}  {'Stake':>8}")
        print("─" * 72)

        for e in sized:
            flag      = " ★" if e["action"] != "neutral" else ""
            stake_str = f"${e['stake']:>7,.0f}" if e["stake"] > 0 else "       —"
            sign      = "+" if e["edge"] >= 0 else ""
            print(f"  {e['player']:<28}  "
                  f"{e['model']*100:>6.1f}%  "
                  f"{e['market']*100:>6.1f}%  "
                  f"{sign}{e['edge']*100:>5.1f}%  "
                  f"{e['action']:<8}  "
                  f"{stake_str}{flag}")

        print("─" * 72)

    actionable  = [e for e in sized if e["action"] != "neutral" and e["stake"] > 0]
    total_stake = sum(e["stake"] for e in actionable)

    print(f"\n  Actionable edges: {len(actionable)}  |  "
          f"Total stake: ${total_stake:,.2f}  "
          f"({total_stake / BANKROLL * 100:.1f}% of bankroll)")

    if check_only:
        print("\n  --check passed: no positions logged.")
    elif actionable:
        csv_path = save_to_csv(sized, tournament_name, exchange, fmt)
        print(f"\n  Positions logged → {csv_path}")
    else:
        print("\n  No actionable edges — nothing logged.")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="PolymarketBot — Chess Tournament Simulator"
    )
    parser.add_argument("--tournament", required=True,
                        help="Tournament display name")
    parser.add_argument("--tour-id", default=None,
                        dest="tour_id",
                        help="8-char Lichess tour ID (e.g. BLA70Vds)")
    parser.add_argument("--no-broadcast", action="store_true",
                        dest="no_broadcast",
                        help="Skip Lichess fetch; use POLYMARKET_SLUGS player list. "
                             "Use when the broadcast isn't set up yet.")
    parser.add_argument("--total-rounds", type=int, default=None,
                        dest="total_rounds_hint",
                        help="Total rounds in the tournament (needed with --no-broadcast "
                             "for single vs double round-robin detection)")
    parser.add_argument("--exchange", default="polymarket",
                        choices=["polymarket", "kalshi"],
                        help="Price source: polymarket (default) or kalshi. "
                             "Kalshi prices are read from KALSHI_PRICES in this file.")
    parser.add_argument("--polymarket", default=None,
                        help="Polymarket event slug (required when --exchange polymarket "
                             "and slugs are configured)")
    parser.add_argument("--format", default="classical",
                        choices=["classical", "rapid", "blitz"],
                        dest="fmt",
                        help="Tournament time control (default: classical)")
    parser.add_argument("--check", action="store_true",
                        help="Show edge table only; do not write to positions.csv")
    parser.add_argument("--force", action="store_true",
                        help=f"Override the >{ROUNDS_GATE}-round gate; "
                             "run analysis mid-tournament for research")

    args = parser.parse_args()
    run(
        args.tournament,
        polymarket_slug=args.polymarket,
        check_only=args.check,
        tour_id=args.tour_id,
        force=args.force,
        fmt=args.fmt,
        exchange=args.exchange,
        no_broadcast=args.no_broadcast,
        total_rounds_hint=args.total_rounds_hint,
    )
