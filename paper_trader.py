"""Live paper trading system for the FIDE Candidates 2026 Polymarket markets.

Each run:
  1. Connects to Google Sheets
  2. Settles any OPEN positions using current live prices
  3. Scrapes live FIDE pairings to detect completed rounds
  4. Runs the Elo / Monte Carlo model
  5. Calculates half-Kelly position sizes for actionable edges
  6. Logs new OPEN positions to the sheet

Usage: python paper_trader.py
"""

import requests
import json
import re
import math
import random
import time
import os
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from datetime import datetime
from bs4 import BeautifulSoup

load_dotenv()

# ─── Direct Sheets connection (no header enforcement) ─────────────────────────

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
_SHEET_NAME       = os.getenv("GOOGLE_SHEET_NAME", "ArbBotLog")
_CREDENTIALS_FILE = os.path.join(os.path.dirname(__file__), "credentials.json")

PAPER_HEADERS = [
    "Timestamp", "Round", "Player", "Side",
    "Entry Price", "Model Fair", "Edge %",
    "Stake $", "Exit Price", "P&L $", "Status",
]

def connect_sheet():
    """Connects directly to Google Sheets without enforcing any header row."""
    try:
        creds  = Credentials.from_service_account_file(_CREDENTIALS_FILE, scopes=_SCOPES)
        client = gspread.authorize(creds)
        return client.open(_SHEET_NAME).sheet1
    except Exception as e:
        print(f"Error connecting to Google Sheets: {e}")
        return None

# ─── Constants ────────────────────────────────────────────────────────────────

SIMULATIONS      = 50000
BANKROLL         = 10_000.0
MIN_EDGE         = 0.05
KELLY_FRACTION   = 0.5
WHITE_BONUS      = 35
DRAW_LOGIT_INTERCEPT  = -0.0198
DRAW_LOGIT_RDIFF_COEF =  0.00687
DRAW_LOGIT_MEAN_COEF  = -0.000421

PAIRINGS_URL = "https://candidates2026.fide.com/pairings"

FIDE_IDS = {
    "Fabiano Caruana":   2020009,
    "Hikaru Nakamura":   2016192,
    "Javokhir Sindarov": 14204118,
    "Praggnanandhaa R":  35009192,
    "Anish Giri":        24116068,
    "Wei Yi":            8603677,
    "Andrey Esipenko":   24175439,
    "Matthias Bluebaum": 24638479,
}

FALLBACK_RATINGS = {
    "Fabiano Caruana":   2805,
    "Hikaru Nakamura":   2810,
    "Javokhir Sindarov": 2780,
    "Praggnanandhaa R":  2751,
    "Anish Giri":        2753,
    "Wei Yi":            2734,
    "Andrey Esipenko":   2698,
    "Matthias Bluebaum": 2700,
}

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

# Full 14-round schedule — used by get_remaining_games()
FULL_SCHEDULE = [
    # Round 1
    ("Javokhir Sindarov",  "Andrey Esipenko"),
    ("Matthias Bluebaum",  "Wei Yi"),
    ("Praggnanandhaa R",   "Anish Giri"),
    ("Fabiano Caruana",    "Hikaru Nakamura"),
    # Round 2
    ("Andrey Esipenko",    "Hikaru Nakamura"),
    ("Anish Giri",         "Fabiano Caruana"),
    ("Wei Yi",             "Praggnanandhaa R"),
    ("Javokhir Sindarov",  "Matthias Bluebaum"),
    # Round 3
    ("Matthias Bluebaum",  "Andrey Esipenko"),
    ("Praggnanandhaa R",   "Javokhir Sindarov"),
    ("Fabiano Caruana",    "Wei Yi"),
    ("Hikaru Nakamura",    "Anish Giri"),
    # Round 4
    ("Andrey Esipenko",    "Anish Giri"),
    ("Wei Yi",             "Hikaru Nakamura"),
    ("Javokhir Sindarov",  "Fabiano Caruana"),
    ("Matthias Bluebaum",  "Praggnanandhaa R"),
    # Round 5
    ("Praggnanandhaa R",   "Andrey Esipenko"),
    ("Fabiano Caruana",    "Matthias Bluebaum"),
    ("Hikaru Nakamura",    "Javokhir Sindarov"),
    ("Anish Giri",         "Wei Yi"),
    # Round 6
    ("Fabiano Caruana",    "Andrey Esipenko"),
    ("Hikaru Nakamura",    "Praggnanandhaa R"),
    ("Anish Giri",         "Matthias Bluebaum"),
    ("Wei Yi",             "Javokhir Sindarov"),
    # Round 7
    ("Andrey Esipenko",    "Wei Yi"),
    ("Javokhir Sindarov",  "Anish Giri"),
    ("Matthias Bluebaum",  "Hikaru Nakamura"),
    ("Praggnanandhaa R",   "Fabiano Caruana"),
    # Round 8
    ("Andrey Esipenko",    "Javokhir Sindarov"),
    ("Wei Yi",             "Matthias Bluebaum"),
    ("Anish Giri",         "Praggnanandhaa R"),
    ("Hikaru Nakamura",    "Fabiano Caruana"),
    # Round 9
    ("Hikaru Nakamura",    "Andrey Esipenko"),
    ("Fabiano Caruana",    "Anish Giri"),
    ("Praggnanandhaa R",   "Wei Yi"),
    ("Matthias Bluebaum",  "Javokhir Sindarov"),
    # Round 10
    ("Andrey Esipenko",    "Matthias Bluebaum"),
    ("Javokhir Sindarov",  "Praggnanandhaa R"),
    ("Wei Yi",             "Fabiano Caruana"),
    ("Anish Giri",         "Hikaru Nakamura"),
    # Round 11
    ("Anish Giri",         "Andrey Esipenko"),
    ("Hikaru Nakamura",    "Wei Yi"),
    ("Fabiano Caruana",    "Javokhir Sindarov"),
    ("Praggnanandhaa R",   "Matthias Bluebaum"),
    # Round 12
    ("Andrey Esipenko",    "Praggnanandhaa R"),
    ("Matthias Bluebaum",  "Fabiano Caruana"),
    ("Javokhir Sindarov",  "Hikaru Nakamura"),
    ("Wei Yi",             "Anish Giri"),
    # Round 13
    ("Wei Yi",             "Andrey Esipenko"),
    ("Anish Giri",         "Javokhir Sindarov"),
    ("Hikaru Nakamura",    "Matthias Bluebaum"),
    ("Fabiano Caruana",    "Praggnanandhaa R"),
    # Round 14
    ("Andrey Esipenko",    "Fabiano Caruana"),
    ("Praggnanandhaa R",   "Hikaru Nakamura"),
    ("Matthias Bluebaum",  "Anish Giri"),
    ("Javokhir Sindarov",  "Wei Yi"),
]

# Hardcoded fallback through Round 4 — used if live scrape fails
HARDCODED_COMPLETED_ROUNDS = {
    1: [
        ("Javokhir Sindarov",  "Andrey Esipenko",   1.0, 0.0),
        ("Matthias Bluebaum",  "Wei Yi",             0.5, 0.5),
        ("Praggnanandhaa R",   "Anish Giri",         1.0, 0.0),
        ("Fabiano Caruana",    "Hikaru Nakamura",    1.0, 0.0),
    ],
    2: [
        ("Andrey Esipenko",    "Hikaru Nakamura",    0.5, 0.5),
        ("Anish Giri",         "Fabiano Caruana",    0.5, 0.5),
        ("Wei Yi",             "Praggnanandhaa R",   0.5, 0.5),
        ("Javokhir Sindarov",  "Matthias Bluebaum",  0.5, 0.5),
    ],
    3: [
        ("Matthias Bluebaum",  "Andrey Esipenko",    0.5, 0.5),
        ("Praggnanandhaa R",   "Javokhir Sindarov",  0.0, 1.0),
        ("Fabiano Caruana",    "Wei Yi",             1.0, 0.0),
        ("Hikaru Nakamura",    "Anish Giri",         0.5, 0.5),
    ],
    4: [
        ("Andrey Esipenko",    "Anish Giri",         0.0, 1.0),
        ("Wei Yi",             "Hikaru Nakamura",    0.5, 0.5),
        ("Javokhir Sindarov",  "Fabiano Caruana",    1.0, 0.0),
        ("Matthias Bluebaum",  "Praggnanandhaa R",   0.5, 0.5),
    ],
}


# ─── Google Sheets setup ──────────────────────────────────────────────────────

def setup_sheet(sheet) -> None:
    """Clears the sheet and writes fresh column headers."""
    sheet.clear()
    sheet.append_row([
        "Timestamp", "Round", "Player", "Side",
        "Entry Price", "Model Fair", "Edge %",
        "Stake $", "Exit Price", "P&L $", "Status",
    ])
    print("  Sheet cleared and headers written.")


# ─── Pairings scraper ─────────────────────────────────────────────────────────

def scrape_pairings() -> dict:
    """Scrapes candidates2026.fide.com/pairings and returns completed games
    grouped by round number: {round_num: [(white, black, ws, bs), ...]}"""
    response = requests.get(
        PAIRINGS_URL,
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    text = soup.get_text(" ", strip=True)

    # Score tokens that appear in the page
    result_map = {
        "1\u20140": (1.0, 0.0),   # 1—0
        "0\u20141": (0.0, 1.0),   # 0—1
        "\u00bd\u2014\u00bd": (0.5, 0.5),  # ½—½
        "1-0":   (1.0, 0.0),
        "0-1":   (0.0, 1.0),
        "1/2-1/2": (0.5, 0.5),
        "draw":  (0.5, 0.5),
    }

    # Pattern: captures "Player A  <result>  Player B"
    pattern = re.compile(
        r'([A-Z][A-Za-z\s,\.]+?)\s+'
        r'(1\u20140|0\u20141|\u00bd\u2014\u00bd|1-0|0-1|1/2-1/2)'
        r'\s+([A-Z][A-Za-z\s,\.]+)',
        re.UNICODE,
    )

    # Locate round headers and bucket games under them
    round_pattern = re.compile(r'Round\s+(\d+)', re.IGNORECASE)
    completed: dict[int, list] = {}
    current_round = None

    for line in text.split("\n"):
        line = line.strip()
        m_round = round_pattern.search(line)
        if m_round:
            current_round = int(m_round.group(1))
            continue
        m_game = pattern.search(line)
        if m_game and current_round is not None:
            white_raw, result_str, black_raw = m_game.groups()
            white = white_raw.strip()
            black = black_raw.strip()
            ws, bs = result_map.get(result_str, (None, None))
            if ws is not None:
                completed.setdefault(current_round, []).append(
                    (white, black, ws, bs)
                )

    total_games = sum(len(v) for v in completed.values())
    print(f"  Scraped {len(completed)} completed round(s), {total_games} total game(s)")
    return completed


# ─── FIDE rating scraper (copied from elo_model.py) ──────────────────────────

def scrape_fide_rating(fide_id: int, name: str = "") -> int | None:
    """Scrapes classical Elo rating from FIDE profile page."""
    url = f"https://ratings.fide.com/profile/{fide_id}"
    headers = {
        "User-Agent":
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
            " AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code != 200:
            print(f"  Failed to scrape {name}, using fallback")
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        html = response.text

        # Approach 1: element near "Classical" / "Std" text
        for tag in soup.find_all(string=re.compile(r"Classical|Std", re.I)):
            block_text = tag.parent.get_text(" ", strip=True)
            matches = re.findall(r"\b2[4-9]\d{2}\b", block_text)
            if matches:
                rating = int(matches[0])
                print(f"  Scraped {name}: {rating}")
                return rating

        # Approach 2: element whose text is exactly a 4-digit Elo
        for tag in soup.find_all(string=re.compile(r"^\s*2[4-9]\d{2}\s*$")):
            rating = int(tag.strip())
            print(f"  Scraped {name}: {rating}")
            return rating

        # Approach 3: first raw-HTML regex match
        matches = re.findall(r"\b2[4-9]\d{2}\b", html)
        if matches:
            rating = int(matches[0])
            print(f"  Scraped {name}: {rating}")
            return rating

        print(f"  Failed to scrape {name}, using fallback")
        return None
    except Exception:
        print(f"  Failed to scrape {name}, using fallback")
        return None


def get_all_ratings() -> dict:
    """Scrapes ratings for all 8 players; falls back to FALLBACK_RATINGS.
    Warns if scraped value differs from fallback by more than 30 points."""
    ratings = {}
    for player, fide_id in FIDE_IDS.items():
        rating = scrape_fide_rating(fide_id, name=player)
        if rating is None:
            rating = FALLBACK_RATINGS[player]
        ratings[player] = rating

    for player, rating in ratings.items():
        fallback = FALLBACK_RATINGS[player]
        diff = abs(rating - fallback)
        if diff > 30:
            print(f"  WARNING: {player} scraped={rating} fallback={fallback} diff={diff}")

    return ratings


# ─── Schedule helpers ─────────────────────────────────────────────────────────

def get_remaining_games(completed_rounds: dict) -> list:
    """Returns FULL_SCHEDULE entries not yet played."""
    completed_pairs = set()
    for games in completed_rounds.values():
        for white, black, ws, bs in games:
            completed_pairs.add((white, black))
    return [(w, b) for w, b in FULL_SCHEDULE if (w, b) not in completed_pairs]


# ─── Core model functions (copied exactly from elo_model.py) ─────────────────

def elo_win_prob(rating_a: float, rating_b: float) -> float:
    """Returns P(A wins) in a single game against B (excluding draws)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def pav_draw_rate(rating_a: float, rating_b: float) -> float:
    """Returns P(draw) using the Pav logistic model (gilgamath.com)."""
    delta = abs(rating_a - rating_b)
    mean  = (rating_a + rating_b) / 2.0
    logit_decisive = (DRAW_LOGIT_INTERCEPT
                      + DRAW_LOGIT_RDIFF_COEF * delta
                      + DRAW_LOGIT_MEAN_COEF  * mean)
    p_decisive = 1.0 / (1.0 + math.exp(-logit_decisive))
    return round(1.0 - p_decisive, 4)


def simulate_game(player_a: str, player_b: str, ratings: dict) -> tuple:
    """Simulates one game. player_a is white (+WHITE_BONUS to effective rating)."""
    white_rating = ratings[player_a] + WHITE_BONUS
    black_rating = ratings[player_b]

    p_a_wins  = elo_win_prob(white_rating, black_rating)
    draw_rate = pav_draw_rate(white_rating, black_rating)

    p_a_decisive = p_a_wins * (1 - draw_rate)
    p_draw       = draw_rate

    r = random.random()
    if r < p_a_decisive:
        return (1.0, 0.0)
    elif r < p_a_decisive + p_draw:
        return (0.5, 0.5)
    else:
        return (0.0, 1.0)


# ─── Simulation ───────────────────────────────────────────────────────────────

def run_simulation(ratings: dict, completed_rounds: dict,
                   remaining_games: list) -> dict:
    """50k Monte Carlo simulation using live completed/remaining game data.
    σ=50 performance variance per iteration; random tiebreak."""
    players    = list(FIDE_IDS.keys())
    win_counts = {p: 0 for p in players}

    for _ in range(SIMULATIONS):
        perf_ratings = {p: random.gauss(ratings[p], 50) for p in players}

        # Seed scores from actual results
        scores = {p: 0.0 for p in players}
        for games in completed_rounds.values():
            for white, black, ws, bs in games:
                if white in scores:
                    scores[white] += ws
                if black in scores:
                    scores[black] += bs

        # Simulate the rest
        for white, black in remaining_games:
            ws, bs = simulate_game(white, black, perf_ratings)
            scores[white] += ws
            scores[black] += bs

        max_score = max(scores.values())
        winners   = [p for p, s in scores.items() if s == max_score]
        win_counts[random.choice(winners)] += 1

    return {p: round(win_counts[p] / SIMULATIONS, 4) for p in players}


# ─── Live price fetcher ───────────────────────────────────────────────────────

def get_live_prices() -> dict:
    """Fetches live YES midpoint prices via CLOB API (token ID from Gamma)."""
    prices = {}
    for player, slug in POLYMARKET_SLUGS.items():
        try:
            url  = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
            data = requests.get(url, timeout=10).json()

            outcomes  = json.loads(data.get("outcomes",     "[]"))
            token_ids = json.loads(data.get("clobTokenIds", "[]"))
            yes_token = dict(zip(outcomes, token_ids)).get("Yes")
            if not yes_token:
                print(f"  {player}: no Yes token")
                continue

            cr    = requests.get("https://clob.polymarket.com/midpoint",
                                 params={"token_id": yes_token}, timeout=10)
            price = float(cr.json()["mid"])
            prices[player] = price
            print(f"  {player}: {price:.3f}")
        except Exception as e:
            print(f"  Error {player}: {e}")
    return prices


# ─── Position sizing ─────────────────────────────────────────────────────────

def calculate_half_kelly(edge: float, market_price: float,
                         model_prob: float, bankroll: float) -> float:
    """Returns half-Kelly stake in dollars.

    For BET YES (edge > 0): buying YES at market_price.
    For BET NO  (edge < 0): buying NO at (1 - market_price).
    """
    if edge >= MIN_EDGE:                # YES bet
        b = (1 - market_price) / market_price
        p = model_prob
        q = 1 - p
    else:                               # NO bet
        b = market_price / (1 - market_price)
        p = 1 - model_prob
        q = model_prob

    full_kelly = max(0.0, (b * p - q) / b)
    return round(full_kelly * KELLY_FRACTION * bankroll, 2)


# ─── Sheet operations ─────────────────────────────────────────────────────────

def settle_open_positions(sheet, current_prices: dict) -> int:
    """Marks every OPEN row as SETTLED, filling Exit Price and P&L."""
    all_values = sheet.get_all_values()
    if len(all_values) < 2:
        return 0

    # Build column index from actual header row
    header  = all_values[0]
    col     = {name: idx for idx, name in enumerate(header)}
    settled = 0

    for i, values in enumerate(all_values[1:], start=1):
        # Pad short rows
        row = values + [""] * (len(header) - len(values))

        if row[col.get("Status", 10)] != "OPEN":
            continue
        player = row[col.get("Player", 2)]
        if player not in current_prices:
            continue

        side         = row[col.get("Side", 3)]
        entry_price  = float(row[col.get("Entry Price", 4)] or 0)
        stake        = float(row[col.get("Stake $", 7)] or 0)
        exit_price   = current_prices[player]

        if side == "YES":
            current_value = stake * (exit_price / entry_price)
        else:                            # NO
            no_entry   = 1 - entry_price
            no_current = 1 - exit_price
            current_value = stake * (no_current / no_entry) if no_entry else 0.0

        pnl       = round(current_value - stake, 2)
        sheet_row = i + 1               # all_values[1:] means i=1 → sheet row 2

        sheet.update_cell(sheet_row, 9,  exit_price)   # Exit Price
        sheet.update_cell(sheet_row, 10, pnl)          # P&L $
        sheet.update_cell(sheet_row, 11, "SETTLED")    # Status
        settled += 1

        sign = "+" if pnl >= 0 else ""
        print(f"  Settled: {player:<23} {side}  "
              f"entry={entry_price:.3f}  exit={exit_price:.3f}  "
              f"P&L=${sign}{pnl:.2f}")

    return settled


def log_new_positions(sheet, round_num: int, edges: dict,
                      market_prices: dict, stakes: dict) -> int:
    """Appends one OPEN row per actionable edge to the sheet."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logged    = 0

    for player, data in sorted(edges.items(),
                                key=lambda x: abs(x[1]["edge"]), reverse=True):
        if data["action"] == "neutral":
            continue
        stake = stakes.get(player, 0)
        if stake <= 0:
            continue

        sheet.append_row([
            timestamp,
            f"Round {round_num}",
            player,
            data["action"],                     # "YES" or "NO"
            market_prices[player],               # entry price (YES price)
            round(data["model"], 4),
            round(data["edge"] * 100, 2),        # edge as %
            stake,
            "",                                  # exit price — filled on settle
            "",                                  # P&L — filled on settle
            "OPEN",
        ])
        logged += 1
        sign = "+" if data["edge"] >= 0 else ""
        print(f"  Logged: {player:<23} {data['action']:<4}  "
              f"@ {market_prices[player]:.3f}  "
              f"model={data['model']:.3f}  "
              f"edge={sign}{data['edge']*100:.1f}%  "
              f"stake=${stake:,.2f}")

    return logged


# ─── Main orchestration ───────────────────────────────────────────────────────

def run() -> None:
    print("=" * 60)
    print("PolymarketBot — Live Paper Trader")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("=" * 60)
    print()

    # ── Step 1: Connect to Google Sheets ──────────────────────────────────────
    print("Connecting to Google Sheets...")
    sheet = connect_sheet()
    if sheet is None:
        print("ERROR: Could not connect to Google Sheets. Exiting.")
        return

    all_values = sheet.get_all_values()
    if not all_values or all_values[0] != PAPER_HEADERS:
        print("  Writing paper-trader headers.")
        setup_sheet(sheet)

    # ── Step 2: Fetch live prices (needed for settlement and new positions) ───
    print()
    print("Fetching live Polymarket prices...")
    market_prices = get_live_prices()
    if not market_prices:
        print("ERROR: Could not fetch any prices. Exiting.")
        return

    # ── Step 3: Settle any open positions ─────────────────────────────────────
    print()
    print("Settling open positions...")
    n_settled = settle_open_positions(sheet, market_prices)
    if n_settled == 0:
        print("  No open positions to settle.")

    # ── Step 4: Scrape live pairings ──────────────────────────────────────────
    print()
    print("Scraping FIDE pairings...")
    try:
        completed_rounds = scrape_pairings()
        if not completed_rounds:
            raise ValueError("No completed rounds parsed from page.")
    except Exception as e:
        print(f"  WARNING: Pairings scrape failed ({e}). Using hardcoded Round 4 data.")
        completed_rounds = HARDCODED_COMPLETED_ROUNDS

    current_round  = max(completed_rounds.keys())
    remaining_games = get_remaining_games(completed_rounds)
    total_completed = sum(len(v) for v in completed_rounds.values())
    print(f"  Through Round {current_round} | "
          f"{total_completed} games completed | "
          f"{len(remaining_games)} remaining")

    # Print current standings
    scores: dict[str, float] = {p: 0.0 for p in FIDE_IDS}
    for games in completed_rounds.values():
        for white, black, ws, bs in games:
            if white in scores:
                scores[white] += ws
            if black in scores:
                scores[black] += bs
    print()
    print("  Standings:")
    for player, score in sorted(scores.items(), key=lambda x: x[1], reverse=True):
        print(f"    {player:<25} {score:.1f}")

    # ── Step 5: Scrape FIDE ratings ───────────────────────────────────────────
    print()
    print("Scraping FIDE ratings...")
    ratings = get_all_ratings()

    # ── Step 6: Run simulation ────────────────────────────────────────────────
    print()
    print(f"Running {SIMULATIONS:,} Monte Carlo simulations...")
    model_probs = run_simulation(ratings, completed_rounds, remaining_games)

    # ── Step 7: Calculate edges and Kelly stakes ──────────────────────────────
    edges  : dict[str, dict] = {}
    stakes : dict[str, float] = {}

    for player, model_prob in model_probs.items():
        if player not in market_prices:
            continue
        mkt  = market_prices[player]
        edge = round(model_prob - mkt, 4)

        if edge >= MIN_EDGE:
            action = "YES"
        elif edge <= -MIN_EDGE:
            action = "NO"
        else:
            action = "neutral"

        edges[player] = {"model": model_prob, "market": mkt,
                         "edge": edge, "action": action}

        if action != "neutral":
            stakes[player] = calculate_half_kelly(edge, mkt, model_prob, BANKROLL)

    # Print edge table
    print()
    print("─" * 68)
    print(f"  {'Player':<23}  {'Model':>7}  {'Market':>7}  "
          f"{'Edge':>7}  {'Action':<8}  {'Stake':>8}")
    print("─" * 68)
    for player, data in sorted(edges.items(),
                                key=lambda x: abs(x[1]["edge"]), reverse=True):
        flag      = " ★" if data["action"] != "neutral" else ""
        stake_str = f"${stakes[player]:>7,.2f}" if player in stakes else "        —"
        sign      = "+" if data["edge"] >= 0 else ""
        print(f"  {player:<23}  "
              f"{data['model']*100:>6.1f}%  "
              f"{data['market']*100:>6.1f}%  "
              f"{sign}{data['edge']*100:>5.1f}%  "
              f"{data['action']:<8}  "
              f"{stake_str}{flag}")
    print("─" * 68)

    actionable    = {p: d for p, d in edges.items() if d["action"] != "neutral"}
    total_stake   = sum(stakes.values())
    print(f"\n  Actionable edges: {len(actionable)}  |  "
          f"Total stake: ${total_stake:,.2f}  "
          f"({total_stake / BANKROLL * 100:.1f}% of bankroll)")

    # ── Step 8: Log new positions ─────────────────────────────────────────────
    print()
    if actionable:
        print(f"Logging {len(actionable)} new position(s)...")
        logged = log_new_positions(
            sheet, current_round + 1, edges, market_prices, stakes
        )
        print(f"  {logged} position(s) written to Google Sheets.")
    else:
        print("No actionable edges — nothing to log.")

    print()
    print("Done.")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
