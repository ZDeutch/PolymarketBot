"""Models the FIDE Candidates 2026 tournament using Elo ratings and Monte Carlo
simulation to estimate each player's win probability, then compares to
Polymarket prices to find edges."""

import requests
import json
import re
import random
import math
from datetime import datetime
from bs4 import BeautifulSoup

# ─── Constants ────────────────────────────────────────────────────────────────

SIMULATIONS  = 50000
MIN_EDGE     = 0.05
WHITE_BONUS  = 35    # Elo points added to white's effective rating (Sonas, 266k games)

# Pav logistic draw-rate model (gilgamath.com, fitted on 1.18 M rated games)
DRAW_LOGIT_INTERCEPT  = -0.0198
DRAW_LOGIT_RDIFF_COEF =  0.00687
DRAW_LOGIT_MEAN_COEF  = -0.000421

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

# Completed game results: (white, black, white_score, black_score)
# white_score: 1.0=win, 0.5=draw, 0.0=loss
COMPLETED_GAMES = [
    ("Javokhir Sindarov",  "Andrey Esipenko",   1.0, 0.0),
    ("Matthias Bluebaum",  "Wei Yi",             0.5, 0.5),
    ("Praggnanandhaa R",   "Anish Giri",         1.0, 0.0),
    ("Fabiano Caruana",    "Hikaru Nakamura",    1.0, 0.0),
    ("Andrey Esipenko",    "Hikaru Nakamura",    0.5, 0.5),
    ("Anish Giri",         "Fabiano Caruana",    0.5, 0.5),
    ("Wei Yi",             "Praggnanandhaa R",   0.5, 0.5),
    ("Javokhir Sindarov",  "Matthias Bluebaum",  0.5, 0.5),
]

# Remaining rounds 3-14 pairings: (white, black)
REMAINING_GAMES = [
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

FALLBACK_RATINGS = {
    "Fabiano Caruana":   2805,
    "Hikaru Nakamura":   2794,
    "Javokhir Sindarov": 2740,
    "Praggnanandhaa R":  2747,
    "Anish Giri":        2760,
    "Wei Yi":            2748,
    "Andrey Esipenko":   2745,
    "Matthias Bluebaum": 2700,
}


# ─── Function 1: Scrape FIDE Rating ───────────────────────────────────────────

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

        # Approach 1: look for element containing "Classical" or "Std"
        # and find a nearby 4-digit rating
        for tag in soup.find_all(string=re.compile(r"Classical|Std", re.I)):
            parent = tag.parent
            # Search parent and siblings for a rating number
            block_text = parent.get_text(" ", strip=True)
            matches = re.findall(r"\b2[4-9]\d{2}\b", block_text)
            if matches:
                rating = int(matches[0])
                print(f"  Scraped {name}: {rating}")
                return rating

        # Approach 2: any element whose text is exactly a 4-digit Elo
        for tag in soup.find_all(string=re.compile(r"^\s*2[4-9]\d{2}\s*$")):
            rating = int(tag.strip())
            print(f"  Scraped {name}: {rating}")
            return rating

        # Approach 3: first match from raw HTML regex
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


# ─── Function 2: Get All Ratings ──────────────────────────────────────────────

def get_all_ratings() -> dict:
    """Scrapes ratings for all 8 players with fallbacks.
    Prints a warning for any player where scraped rating differs
    from the fallback by more than 30 points."""
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


# ─── Function 3a: Pav Draw Rate ───────────────────────────────────────────────

def pav_draw_rate(rating_a: float, rating_b: float) -> float:
    """Returns P(draw) for a single game using the Pav logistic model
    (gilgamath.com, fitted on 1.18 million rated games).
    Inputs are the effective ratings (white already boosted by WHITE_BONUS)."""
    delta = abs(rating_a - rating_b)
    mean  = (rating_a + rating_b) / 2.0
    logit_decisive = (DRAW_LOGIT_INTERCEPT
                      + DRAW_LOGIT_RDIFF_COEF * delta
                      + DRAW_LOGIT_MEAN_COEF  * mean)
    p_decisive = 1.0 / (1.0 + math.exp(-logit_decisive))
    return round(1.0 - p_decisive, 4)


# ─── Function 3b: Elo Win Probability ─────────────────────────────────────────

def elo_win_prob(rating_a: float, rating_b: float) -> float:
    """Returns P(A wins) in a single game against B (excluding draws)."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


# ─── Function 4: Simulate One Game ────────────────────────────────────────────

def simulate_game(player_a: str, player_b: str, ratings: dict) -> tuple:
    """Simulates one game. Returns (score_a, score_b).
    player_a is white; WHITE_BONUS is added to white's effective rating.
    Draw rate is computed per-game from the Pav logistic model."""
    white_rating = ratings[player_a] + WHITE_BONUS
    black_rating = ratings[player_b]

    p_a_wins  = elo_win_prob(white_rating, black_rating)
    draw_rate = pav_draw_rate(white_rating, black_rating)

    p_a_decisive = p_a_wins * (1 - draw_rate)
    p_b_decisive = (1 - p_a_wins) * (1 - draw_rate)
    p_draw       = draw_rate

    r = random.random()
    if r < p_a_decisive:
        return (1.0, 0.0)
    elif r < p_a_decisive + p_draw:
        return (0.5, 0.5)
    else:
        return (0.0, 1.0)


# ─── Function 5: Run Monte Carlo Simulation ───────────────────────────────────

def run_simulation(ratings: dict) -> dict:
    """Runs Monte Carlo simulation of the remaining tournament games.
    Each iteration samples per-player performance ratings from N(rating, σ=50)
    to capture tournament-to-tournament form variation (XanthH/candidates_simulation)."""
    players = list(FIDE_IDS.keys())
    win_counts = {p: 0 for p in players}

    for _ in range(SIMULATIONS):

        # Sample performance ratings for this simulation (σ=50 per XanthH model)
        perf_ratings = {p: random.gauss(ratings[p], 50) for p in players}

        # Start with actual scores from completed games
        scores = {p: 0.0 for p in players}
        for white, black, ws, bs in COMPLETED_GAMES:
            scores[white] += ws
            scores[black] += bs

        # Simulate remaining games using this iteration's performance ratings
        for white, black in REMAINING_GAMES:
            ws, bs = simulate_game(white, black, perf_ratings)
            scores[white] += ws
            scores[black] += bs

        # Find winner (highest score)
        max_score = max(scores.values())
        winners = [p for p, s in scores.items() if s == max_score]

        # Random tiebreak if needed
        winner = random.choice(winners)
        win_counts[winner] += 1

    return {p: round(win_counts[p] / SIMULATIONS, 4) for p in players}


# ─── Function 6: Fetch Polymarket Prices ──────────────────────────────────────

def get_polymarket_prices() -> dict:
    """Fetches live YES midpoint prices for all players via the CLOB API.
    Step 1: resolve Yes token ID from Gamma API.
    Step 2: fetch live midpoint from CLOB /midpoint endpoint."""
    prices = {}
    for player, slug in POLYMARKET_SLUGS.items():
        try:
            # Step 1: get token IDs from Gamma API
            url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
            r = requests.get(url, timeout=10)
            r.raise_for_status()
            data = r.json()

            outcomes   = json.loads(data.get("outcomes",     "[]"))
            token_ids  = json.loads(data.get("clobTokenIds", "[]"))
            token_map  = dict(zip(outcomes, token_ids))
            yes_token  = token_map.get("Yes")

            if not yes_token:
                print(f"  {player}: no Yes token found")
                continue

            # Step 2: get live midpoint from CLOB
            clob_url = "https://clob.polymarket.com/midpoint"
            cr = requests.get(clob_url, params={"token_id": yes_token}, timeout=10)
            cr.raise_for_status()
            price = float(cr.json()["mid"])
            prices[player] = price
            print(f"  {player}: {price:.3f}")

        except Exception as e:
            print(f"  Error {player}: {e}")

    return prices


# ─── Function 7: Find Edges ────────────────────────────────────────────────────

def find_edges(model_probs: dict, market_prices: dict) -> list:
    """Compares model probabilities to market prices.
    Returns list of dicts sorted by abs(edge) descending."""
    edges = []
    for player in model_probs:
        if player not in market_prices:
            continue
        model  = model_probs[player]
        market = market_prices[player]
        edge   = round(model - market, 4)

        if edge >= MIN_EDGE:
            action = "BET YES"
        elif edge <= -MIN_EDGE:
            action = "BET NO"
        else:
            action = "neutral"

        edges.append({
            "player": player,
            "model":  model,
            "market": market,
            "edge":   edge,
            "action": action,
        })

    edges.sort(key=lambda x: abs(x["edge"]), reverse=True)
    return edges


# ─── Function 8: Run ──────────────────────────────────────────────────────────

def run() -> list:
    """Main function. Returns edge list for integration."""
    print("FIDE Candidates 2026 — Elo Model")
    print(f"Simulations: {SIMULATIONS} | Draw model: Pav logistic | Min edge: {MIN_EDGE}")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print()

    # Verify Pav draw rate at Candidates level: two equal 2760-rated players
    sample_draw = pav_draw_rate(2760 + WHITE_BONUS, 2760)
    print(f"Draw rate check: two 2760-rated players (white +{WHITE_BONUS}) → {sample_draw:.4f}")
    print()

    print("Scraping FIDE ratings...")
    ratings = get_all_ratings()

    print()
    print("Ratings:")
    for p, r in sorted(ratings.items(), key=lambda x: x[1], reverse=True):
        print(f"  {p:<25} {r}")

    print()
    print(f"Running {SIMULATIONS} Monte Carlo simulations...")
    model_probs = run_simulation(ratings)

    print()
    print("Fetching Polymarket prices...")
    market_prices = get_polymarket_prices()

    print()
    edges = find_edges(model_probs, market_prices)

    print("─" * 62)
    print(f"  {'Player':<23}  {'Model':>7}  {'Market':>7}  {'Edge':>7}  Action")
    print("─" * 62)
    for e in edges:
        flag = " ★" if e["action"] != "neutral" else ""
        print(
            f"  {e['player']:<23}  "
            f"{e['model']  * 100:>6.1f}%  "
            f"{e['market'] * 100:>6.1f}%  "
            f"{e['edge']   * 100:>+6.1f}%  "
            f"{e['action']}{flag}"
        )
    print("─" * 62)

    actionable = [e for e in edges if e["action"] != "neutral"]
    print(f"\nActionable edges (>{MIN_EDGE * 100:.0f}%): {len(actionable)}")

    print()
    print("Model methodology:")
    print("  Draw rate:            Pav logistic model (rating diff + avg rating)")
    print("  White bonus:          +35 Elo (Sonas, 266k games)")
    print("  Performance variance: N(rating, sigma=50) per iteration")
    print(f"  Simulations:          {SIMULATIONS}")
    print("  Tiebreak:             random (simplified — rapid/blitz not modeled)")
    print("  Independent games:    momentum effects not modeled")
    print("    (Kalwij 2025: no detectable sequential effects)")
    print("  Edges >10% may still reflect model limitations")

    return edges
