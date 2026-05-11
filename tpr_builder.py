"""
tpr_builder.py — builds tpr_data.json from elite Lichess broadcast PGN data.

Run ONCE manually to build the dataset:
    python tpr_builder.py

Output: tpr_data.json (read by chess_simulator.py at runtime)
Do NOT run automatically.

Algorithm:
  1. Fetch PGN for every round of each elite tournament
     from the Lichess broadcast API.
  2. Filter to games involving at least one 2600+ rated player.
  3. Compute max-likelihood TPR for each player using only
     games against 2600+ opposition.
  4. Compute personal draw rate against 2600+ opposition.
  5. Write results to tpr_data.json.
"""

import os
import requests
import json
import re
import math
import time
from collections import defaultdict


# ─── Constants ────────────────────────────────────────────────────────────────

HEADERS = {"User-Agent": "PolymarketBot/1.0"}

# Additional round IDs to fetch from Lichess beyond what auto_tours discovers.
# auto_tours (below) already handles Candidates 2026, Sigeman 2026, and any
# tour registered in the freshness ledger — so this is normally empty.
# Add entries here only for tournaments NOT reachable via a tour ID
# (e.g. individual round IDs from a tournament with no Lichess broadcast page).
# Most historical data comes from elite_games_2020_present.pgn, not Lichess fetches.
HARDCODED_ROUNDS: dict[str, list[str]] = {}

MIN_OPPONENT_RATING = 2600
MIN_GAMES_FOR_TPR   = 5   # need at least 5 games to compute reliable TPR

# Recency weighting: weight = exp(-age_days / RECENCY_TIME_CONSTANT_DAYS)
# 365-day time constant ≈ 8.3-month half-life. Games from 1 year ago get
# weight 0.37, 2 years ago 0.14. Reference date is set in run() at build time.
RECENCY_TIME_CONSTANT_DAYS = 365.0
MIN_WEIGHTED_GAMES_FOR_TPR = 3.0   # weighted-game-count threshold for TPR
MIN_WEIGHTED_GAMES_FOR_DRAW = 2.0  # weighted-game-count threshold for draw rate
_REFERENCE_DATE = None             # populated by run(); a datetime.date
_RECENCY_ENABLED = True            # toggled by run(recency=False) for A/B
_ASOF_DATE = None                  # populated by run(asof=...); excludes games on/after this date

# Path to the local PGN cache — 6 500+ elite games, updated manually.
# When present, used as the primary data source (no network, more history).
ELITE_PGN_PATH: str = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "elite_games_2020_present.pgn"
)


def _parse_pgn_date(date_str: str):
    """Parses a PGN Date header like '2024.01.20' → date object.

    Returns None on failure. PGN spec uses '????' for unknown components.
    """
    from datetime import date as _date
    if not date_str:
        return None
    parts = date_str.replace("-", ".").split(".")
    if len(parts) < 3:
        return None
    try:
        y = int(parts[0])
        m = int(parts[1]) if parts[1].isdigit() else 6
        d = int(parts[2]) if parts[2].isdigit() else 15
        m = max(1, min(12, m))
        d = max(1, min(28, d))
        return _date(y, m, d)
    except (ValueError, TypeError):
        return None


def _compute_recency_weight(game_date) -> float:
    """exp(-age_days / RECENCY_TIME_CONSTANT_DAYS). Returns 1.0 if no date.

    Returns 1.0 for all games when _RECENCY_ENABLED is False (A/B baseline).
    """
    import math as _math
    if not _RECENCY_ENABLED:
        return 1.0
    if game_date is None or _REFERENCE_DATE is None:
        return 1.0
    age_days = (_REFERENCE_DATE - game_date).days
    if age_days <= 0:
        return 1.0
    return _math.exp(-age_days / RECENCY_TIME_CONSTANT_DAYS)


# ─── Data fetching ─────────────────────────────────────────────────────────────

def get_round_pgn(round_id: str) -> str:
    """Fetches PGN text for a broadcast round.

    URL: https://lichess.org/broadcast/-/-/{round_id}.pgn
    Sleeps 1 second after each request to be polite to Lichess.
    Returns raw PGN text, or "" on error.
    """
    url = f"https://lichess.org/broadcast/-/-/{round_id}.pgn"
    try:
        resp = requests.get(
            url,
            headers={**HEADERS, "Accept": "application/x-chess-pgn"},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:
        print(f"    WARNING: could not fetch PGN for round '{round_id}': {e}")
        return ""
    finally:
        time.sleep(1)


# ─── Name normalisation ───────────────────────────────────────────────────────

# Maps PGN "Last, First" variants → canonical "First Last" form used
# throughout the codebase.  Keys are exact strings as they appear in
# Lichess broadcast PGN headers.
_NAME_REPLACEMENTS = {
    "Gukesh, D":               "Gukesh D",
    "Praggnanandhaa, R":       "Praggnanandhaa R",
    "R Praggnanandhaa":        "Praggnanandhaa R",
    "Dommaraju, Gukesh":       "Gukesh D",
    "Nepomniachtchi, Ian":     "Ian Nepomniachtchi",
    "Firouzja, Alireza":       "Alireza Firouzja",
    "Caruana, Fabiano":        "Fabiano Caruana",
    "Nakamura, Hikaru":        "Hikaru Nakamura",
    "Giri, Anish":             "Anish Giri",
    "Abdusattorov, Nodirbek":  "Nodirbek Abdusattorov",
    "Erigaisi, Arjun":         "Arjun Erigaisi",
    "Keymer, Vincent":         "Vincent Keymer",
    "Sindarov, Javokhir":      "Javokhir Sindarov",
    "Esipenko, Andrey":        "Andrey Esipenko",
    "Bluebaum, Matthias":      "Matthias Bluebaum",
    "Wei, Yi":                 "Wei Yi",
    "Erigaisi Arjun":          "Arjun Erigaisi",
    "Abdusattorov Nodirbek":   "Nodirbek Abdusattorov",
    "Keymer Vincent":          "Vincent Keymer",
    "Tabatabaei M. Amin":      "M. Amin Tabatabaei",
    # Sigeman 2026 players
    "Grandelius, Nils":        "Nils Grandelius",
    "Woodward, Andy":          "Andy Woodward",
    "Zhu, Jiner":              "Zhu Jiner",   # keep Chinese surname-first order
    "Van Foreest, Jorden":     "Jorden van Foreest",
    "van Foreest, Jorden":     "Jorden van Foreest",
    "Erdogmus, Yagiz Kaan":    "Yagiz Kaan Erdogmus",
}


def normalize_name(name: str) -> str:
    """Normalises a player name from PGN to canonical form.

    1. Checks the explicit _NAME_REPLACEMENTS lookup first.
    2. Falls back to reversing "Last, First" → "First Last"
       for any name containing exactly one comma.
    """
    # Explicit replacements first
    if name in _NAME_REPLACEMENTS:
        return _NAME_REPLACEMENTS[name]

    # General "Last, First" → "First Last" inversion
    # Only apply if exactly one comma is present
    parts = name.split(",")
    if len(parts) == 2:
        last  = parts[0].strip()
        first = parts[1].strip()
        if first and last:
            return f"{first} {last}"

    return name


# ─── PGN parsing ──────────────────────────────────────────────────────────────

def parse_pgn_games(pgn_text: str, tournament: str | None = None) -> list:
    """Parses PGN text into a list of normalised game dicts.

    Uses regex to extract PGN tag-pair headers. Each game is identified
    by a block beginning with [Event. Games are split on that boundary.

    Skips games where:
      - Result is "*" (in progress / abandoned)
      - Either WhiteElo or BlackElo is missing or non-numeric

    Returns list of dicts:
    {
      "white": str, "black": str,
      "white_elo": int, "black_elo": int,
      "result": str,
      "white_score": float, "black_score": float,
    }
    """
    if not pgn_text.strip():
        return []

    header_re = re.compile(r'\[(\w+)\s+"([^"]+)"\]')

    # Split into individual game blocks at each [Event tag
    game_blocks = re.split(r'(?=\[Event )', pgn_text)

    games = []
    for block in game_blocks:
        block = block.strip()
        if not block:
            continue

        headers = dict(header_re.findall(block))

        white  = normalize_name(headers.get("White", "").strip())
        black  = normalize_name(headers.get("Black", "").strip())
        result = headers.get("Result", "*").strip()

        if not white or not black:
            continue
        if result == "*":
            continue

        # Parse Elo — Lichess may write "?" for unknown
        try:
            white_elo = int(headers["WhiteElo"])
        except (KeyError, ValueError):
            continue
        try:
            black_elo = int(headers["BlackElo"])
        except (KeyError, ValueError):
            continue

        # Convert result to numeric scores
        if result == "1-0":
            white_score, black_score = 1.0, 0.0
        elif result == "0-1":
            white_score, black_score = 0.0, 1.0
        elif result == "1/2-1/2":
            white_score, black_score = 0.5, 0.5
        else:
            continue

        game_date = _parse_pgn_date(headers.get("Date", "").strip())
        weight    = _compute_recency_weight(game_date)

        games.append({
            "white":       white,
            "black":       black,
            "white_elo":   white_elo,
            "black_elo":   black_elo,
            "result":      result,
            "white_score": white_score,
            "black_score": black_score,
            "date":        game_date.isoformat() if game_date else None,
            "weight":      weight,
            # If tournament is explicitly passed (Lichess fetch), use it.
            # Otherwise fall back to the per-game BroadcastName header (local PGN).
            "tournament":  tournament if tournament is not None else headers.get("BroadcastName"),
        })

    return games


# ─── TPR computation ──────────────────────────────────────────────────────────

def compute_tpr(games: list, player_name: str) -> float | None:
    """Computes maximum-likelihood Tournament Performance Rating.

    Based on Steven Pav's ML estimator:
      TPR = argmax_R Σᵢ [ sᵢ·log(E(R,rᵢ)) + (1−sᵢ)·log(1−E(R,rᵢ)) ]

    Where E(R, r) = 1 / (1 + 10^((r − R) / 400))

    Only games against opponents rated >= MIN_OPPONENT_RATING are used.
    Returns None if fewer than MIN_GAMES_FOR_TPR qualifying games exist.

    Uses scipy.optimize.minimize_scalar if available; falls back to
    binary search on the score-equation root otherwise.
    """
    # Collect (opponent_elo, player_score, weight) triples
    pairs = []
    for g in games:
        if g["white"] == player_name:
            opp_elo = g["black_elo"]
            score   = g["white_score"]
        elif g["black"] == player_name:
            opp_elo = g["white_elo"]
            score   = g["black_score"]
        else:
            continue

        if opp_elo is None or opp_elo < MIN_OPPONENT_RATING:
            continue
        w = g.get("weight", 1.0)
        pairs.append((opp_elo, score, w))

    if len(pairs) < MIN_GAMES_FOR_TPR:
        return None

    total_weight = sum(w for _, _, w in pairs)
    if total_weight < MIN_WEIGHTED_GAMES_FOR_TPR:
        return None

    def neg_log_likelihood(R: float) -> float:
        total = 0.0
        for r_i, s_i, w_i in pairs:
            E = 1.0 / (1.0 + 10.0 ** ((r_i - R) / 400.0))
            E = max(1e-9, min(1.0 - 1e-9, E))   # guard against log(0)
            total += w_i * (s_i * math.log(E) + (1.0 - s_i) * math.log(1.0 - E))
        return -total

    # Try scipy first
    try:
        from scipy.optimize import minimize_scalar
        result = minimize_scalar(
            neg_log_likelihood, bounds=(1800, 3200), method="bounded"
        )
        return round(result.x, 1)
    except ImportError:
        pass

    # Binary search fallback.
    # The gradient dL/dR = Σ wᵢ·(sᵢ − E(R,rᵢ)) · ln(10)/400 equals zero when
    # weighted expected score = weighted actual score.
    weighted_score = sum(w * s for _, s, w in pairs)

    lo, hi = 1800.0, 3200.0
    for _ in range(60):   # 60 iterations → precision < 0.01 Elo
        mid       = (lo + hi) / 2.0
        weighted_E = sum(
            w_i * (1.0 / (1.0 + 10.0 ** ((r_i - mid) / 400.0)))
            for r_i, _, w_i in pairs
        )
        if weighted_E < weighted_score:
            lo = mid
        else:
            hi = mid

    return round((lo + hi) / 2.0, 1)


def _compute_per_tournament_tprs(
    games: list, player_name: str, min_games_per_tour: int = 4
) -> list[tuple[float, float]]:
    """Returns list of (tpr, total_weight) for each tournament with enough games.

    Single-tournament TPR is recovered by re-running the weighted MLE on just
    that tournament's games. Uses binary search (no scipy dep) since we may
    call this many times per player.
    """
    by_tour: dict[str, list] = {}
    for g in games:
        if g["white"] == player_name:
            opp_elo, score = g["black_elo"], g["white_score"]
        elif g["black"] == player_name:
            opp_elo, score = g["white_elo"], g["black_score"]
        else:
            continue
        if opp_elo is None or opp_elo < MIN_OPPONENT_RATING:
            continue
        tour = g.get("tournament") or "_unknown"
        by_tour.setdefault(tour, []).append((opp_elo, score, g.get("weight", 1.0)))

    out: list[tuple[float, float]] = []
    for tour, triples in by_tour.items():
        if len(triples) < min_games_per_tour:
            continue
        total_w = sum(w for _, _, w in triples)
        if total_w <= 0:
            continue
        weighted_score = sum(w * s for _, s, w in triples)
        # Avoid degenerate 100% / 0% scores
        ws_frac = weighted_score / total_w
        if ws_frac >= 0.999 or ws_frac <= 0.001:
            continue
        lo, hi = 1800.0, 3200.0
        for _ in range(40):
            mid = (lo + hi) / 2.0
            we = sum(
                w_i * (1.0 / (1.0 + 10.0 ** ((r_i - mid) / 400.0)))
                for r_i, _, w_i in triples
            )
            if we < weighted_score:
                lo = mid
            else:
                hi = mid
        out.append(((lo + hi) / 2.0, total_w))
    return out


def _weighted_stdev(values_weights: list[tuple[float, float]]) -> float | None:
    """Weighted sample standard deviation. Returns None if <2 entries."""
    if len(values_weights) < 2:
        return None
    total_w = sum(w for _, w in values_weights)
    if total_w <= 0:
        return None
    mean = sum(v * w for v, w in values_weights) / total_w
    var = sum(w * (v - mean) ** 2 for v, w in values_weights) / total_w
    # Bias correction: multiply by N/(N-1) for sample stdev
    n = len(values_weights)
    if n > 1:
        var *= n / (n - 1)
    return math.sqrt(max(0.0, var))


def compute_volatility(games: list, player_name: str, tpr: float | None) -> float | None:
    """Computes per-player performance σ (Elo) for simulator noise.

    Combines three signals:
      1. Standard error of TPR via Fisher information (weighted MLE):
           SE_TPR = (400/ln10) / sqrt(Σ wᵢ · Eᵢ(1−Eᵢ))
         Players with thin / heavily-decayed data → higher SE → wider σ.
      2. Excess score variance above Bernoulli baseline, converted to
         per-game Elo and divided by sqrt(Σw) to get an iteration-level σ.
      3. Per-tournament TPR standard deviation: spread of single-event TPRs
         across tournaments. Captures real performance volatility (hot / cold
         streaks) better than within-event Bernoulli noise.

    Final: σ_total = sqrt(35² + (0.4·SE_TPR)² + σ_excess² + σ_tour²),
    clamped to [30, 75]. Returns None if insufficient data.
    """
    if tpr is None:
        return None

    triples = []   # (opp_elo, score, weight)
    for g in games:
        if g["white"] == player_name:
            opp_elo = g["black_elo"]
            score   = g["white_score"]
        elif g["black"] == player_name:
            opp_elo = g["white_elo"]
            score   = g["black_score"]
        else:
            continue
        if opp_elo is None or opp_elo < MIN_OPPONENT_RATING:
            continue
        triples.append((opp_elo, score, g.get("weight", 1.0)))

    if len(triples) < MIN_GAMES_FOR_TPR:
        return None
    total_w = sum(w for _, _, w in triples)
    if total_w < MIN_WEIGHTED_GAMES_FOR_TPR:
        return None

    log10 = math.log(10)

    info        = 0.0   # weighted Fisher info Σ wᵢ Eᵢ(1−Eᵢ)
    obs_var_sum = 0.0   # Σ wᵢ (sᵢ − Eᵢ)²
    exp_var_sum = 0.0   # Σ wᵢ Eᵢ(1 − Eᵢ)
    for r_i, s_i, w_i in triples:
        E = 1.0 / (1.0 + 10.0 ** ((r_i - tpr) / 400.0))
        E = max(1e-6, min(1.0 - 1e-6, E))
        info        += w_i * E * (1.0 - E)
        obs_var_sum += w_i * (s_i - E) ** 2
        exp_var_sum += w_i * E * (1.0 - E)

    if info <= 0:
        return None

    # Standard error of TPR: Cramér-Rao bound on weighted MLE
    se_tpr = (400.0 / log10) / math.sqrt(info)

    # Excess per-game score variance → per-iteration Elo σ
    excess = max(0.0, (obs_var_sum - exp_var_sum) / total_w)
    # Per-game Elo equivalent: σ_score / (E(1−E) · ln10/400).
    # Use mean E(1−E) = exp_var_sum/total_w as scale; divide by sqrt(N_eff)
    # to convert per-game to per-iteration variance.
    mean_info_per_game = exp_var_sum / total_w
    if excess > 0 and mean_info_per_game > 0:
        sigma_excess_per_game = math.sqrt(excess) * 400.0 / (log10 * mean_info_per_game)
        sigma_excess = sigma_excess_per_game / math.sqrt(total_w)
    else:
        sigma_excess = 0.0

    # Per-tournament TPR stdev — captures inter-event performance volatility
    # (hot/cold streaks) which Bernoulli noise inside a single event misses.
    tour_tprs = _compute_per_tournament_tprs(games, player_name)
    sigma_tour = _weighted_stdev(tour_tprs) if len(tour_tprs) >= 2 else None
    # Cap any single-tournament outlier at 200 Elo to protect against the
    # occasional 6-game blowout sample.
    sigma_tour_capped = min(sigma_tour, 200.0) if sigma_tour else 0.0

    # Combine: 35 Elo baseline + 20% of SE_TPR + full excess + 60% of σ_tour.
    # SE_TPR is the formal Cramér-Rao bound but overstates per-iteration
    # performance noise — players don't actually swing ±150 Elo per event.
    # 0.2× scaling keeps it as a relative confidence signal without pegging
    # most players to the cap. σ_tour at 0.6× because each event's TPR is
    # itself a noisy estimator of true ability.
    sigma_total = math.sqrt(
        35.0 ** 2
        + (0.2 * se_tpr) ** 2
        + sigma_excess ** 2
        + (0.6 * sigma_tour_capped) ** 2
    )
    return round(max(35.0, min(85.0, sigma_total)), 1)


def compute_draw_rate(games: list, player_name: str) -> float | None:
    """Computes the player's recency-weighted draw rate against 2600+ opposition.

    Returns Σ wᵢ·𝟙(draw) / Σ wᵢ for qualifying games.
    Returns None if fewer than 5 raw qualifying games or insufficient
    total weighted games.
    """
    entries = []   # list of (score, weight)
    for g in games:
        if g["white"] == player_name:
            opp_elo = g["black_elo"]
            score   = g["white_score"]
        elif g["black"] == player_name:
            opp_elo = g["white_elo"]
            score   = g["black_score"]
        else:
            continue

        if opp_elo is not None and opp_elo >= MIN_OPPONENT_RATING:
            entries.append((score, g.get("weight", 1.0)))

    if len(entries) < 5:
        return None

    total_weight = sum(w for _, w in entries)
    if total_weight < MIN_WEIGHTED_GAMES_FOR_DRAW:
        return None

    weighted_draws = sum(w for s, w in entries if s == 0.5)
    return round(weighted_draws / total_weight, 4)


# ─── Player stats aggregation ─────────────────────────────────────────────────

def build_player_stats(all_games: list) -> dict:
    """Computes per-player TPR, draw rate, game count, and avg opponent.

    Returns:
    {
      player_name: {
        "tpr":          float | None,
        "draw_rate":    float | None,
        "elite_games":  int,
        "avg_opponent": float | None,
      }
    }
    """
    # Collect all unique player names
    player_names: set[str] = set()
    for g in all_games:
        player_names.add(g["white"])
        player_names.add(g["black"])

    stats = {}
    for player in sorted(player_names):
        tpr        = compute_tpr(all_games, player)
        draw_rate  = compute_draw_rate(all_games, player)
        volatility = compute_volatility(all_games, player, tpr)

        elite_games     = 0
        weighted_games  = 0.0
        opp_ratings     = []
        for g in all_games:
            if g["white"] == player:
                opp_elo = g["black_elo"]
            elif g["black"] == player:
                opp_elo = g["white_elo"]
            else:
                continue
            elite_games    += 1
            weighted_games += g.get("weight", 1.0)
            if opp_elo is not None and opp_elo >= MIN_OPPONENT_RATING:
                opp_ratings.append(opp_elo)

        avg_opponent = (
            round(sum(opp_ratings) / len(opp_ratings), 1)
            if opp_ratings else None
        )

        stats[player] = {
            "tpr":             tpr,
            "draw_rate":       draw_rate,
            "volatility":      volatility,
            "elite_games":     elite_games,
            "weighted_games":  round(weighted_games, 2),
            "avg_opponent":    avg_opponent,
        }

    return stats


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(asof: "datetime.date | str | None" = None,
        recency: bool = True,
        output_path: str = "tpr_data.json") -> None:
    """Fetches PGN data, computes TPR, writes tpr_data.json.

    Args:
        asof: If set, exclude games on or after this date (date object or
            'YYYY-MM-DD' string). Reference date for recency decay also
            becomes asof. Used for out-of-sample backtesting.
        recency: If False, all games get weight=1.0 (A/B baseline).
        output_path: Where to write the JSON. Defaults to tpr_data.json.
    """
    from datetime import datetime, date

    global _REFERENCE_DATE, _RECENCY_ENABLED, _ASOF_DATE
    _RECENCY_ENABLED = recency

    if asof is None:
        _ASOF_DATE = None
        _REFERENCE_DATE = date.today()
    else:
        if isinstance(asof, str):
            asof = date.fromisoformat(asof)
        _ASOF_DATE = asof
        _REFERENCE_DATE = asof

    print("Building elite TPR database...")
    print(f"Reference date for recency weighting: {_REFERENCE_DATE.isoformat()}")
    if _ASOF_DATE:
        print(f"AS-OF cutoff: excluding games on/after {_ASOF_DATE.isoformat()}")
    if recency:
        print(f"Time constant: {RECENCY_TIME_CONSTANT_DAYS:.0f} days "
              f"(half-life ≈ {RECENCY_TIME_CONSTANT_DAYS * 0.693 / 30:.1f} months)")
    else:
        print("Recency weighting: DISABLED (all games equal weight)")
    print("Loading games from local PGN + Lichess auto-fetch...")

    # ── Auto-fetch round IDs from freshness ledger ────────────────────────────
    # Any tour_id::<tournament> entry in .freshness.json is resolved to its
    # completed-round IDs and merged into HARDCODED_ROUNDS. This is how new
    # tournaments get picked up without code edits — just:
    #   python -m freshness mark tour_id "Tournament Name" --value <8-char-id>
    print("\nResolving auto-fetch tour IDs...")
    auto_tours: dict[str, str] = {
        "Candidates 2026": "BLA70Vds",
        "Sigeman 2026":    "1X75mYa7",
    }
    try:
        import freshness as _fresh
        ledger = _fresh._load_ledger()
        for k, v in ledger.items():
            if k.startswith("tour_id::") and v.get("value"):
                tour_name = k[len("tour_id::"):]
                auto_tours[tour_name] = v["value"]
    except Exception as e:
        print(f"  (could not read freshness ledger: {e})")

    from fetchers.chess_fetcher import get_tournament
    for tour_name, tour_id in auto_tours.items():
        try:
            t = get_tournament(tour_id)
            if t and t.get("rounds"):
                ids = [r["id"] for r in t["rounds"] if r.get("finished")]
                HARDCODED_ROUNDS[tour_name] = ids
                print(f"  {tour_name:<45} {tour_id}  → {len(ids)} completed round(s)")
            else:
                HARDCODED_ROUNDS.setdefault(tour_name, [])
                print(f"  {tour_name:<45} {tour_id}  → could not fetch")
        except Exception as e:
            print(f"  {tour_name:<45} {tour_id}  → error: {e}")

    all_games: list = []
    asof_iso = _ASOF_DATE.isoformat() if _ASOF_DATE else None
    skipped_asof = 0
    dedup: set[tuple] = set()   # (white, black, date, result) — prevents double-counting

    # ── Step 1: Load from local PGN cache (fast, no network) ─────────────────
    pgn_loaded = False
    if os.path.exists(ELITE_PGN_PATH):
        print(f"\nLoading local PGN: {os.path.basename(ELITE_PGN_PATH)}")
        with open(ELITE_PGN_PATH, "r", encoding="utf-8", errors="ignore") as _f:
            _local_pgn_text = _f.read()
        _local_games = parse_pgn_games(_local_pgn_text)   # BroadcastName used as tournament
        for g in _local_games:
            if not (
                (g["white_elo"] and g["white_elo"] >= MIN_OPPONENT_RATING)
                or (g["black_elo"] and g["black_elo"] >= MIN_OPPONENT_RATING)
            ):
                continue
            if asof_iso and g.get("date") and g["date"] >= asof_iso:
                skipped_asof += 1
                continue
            key = (g["white"], g["black"], g.get("date"), g["result"])
            if key not in dedup:
                dedup.add(key)
                all_games.append(g)
        pgn_loaded = True
        _pgn_tours = sorted({g["tournament"] for g in all_games if g["tournament"]})
        print(f"  {len(all_games)} elite games from {len(_pgn_tours)} tournament(s)")

    # ── Step 2: Fetch remaining rounds from Lichess (with dedup) ─────────────
    for tour_name, round_ids in HARDCODED_ROUNDS.items():
        print(f"\n{tour_name} ({len(round_ids)} rounds)")
        tour_games = 0
        for round_id in round_ids:
            pgn = get_round_pgn(round_id)
            if not pgn:
                continue
            games = parse_pgn_games(pgn, tournament=tour_name)
            # Keep games where at least one player is rated 2600+
            elite = [
                g for g in games
                if (g["white_elo"] and g["white_elo"] >= MIN_OPPONENT_RATING)
                or (g["black_elo"] and g["black_elo"] >= MIN_OPPONENT_RATING)
            ]
            # As-of filter: drop games on/after the cutoff date.
            if asof_iso:
                cutoff_pgn = asof_iso.replace("-", ".")
                before = len(elite)
                elite = [g for g in elite if not g.get("date") or g["date"] < cutoff_pgn]
                skipped_asof += before - len(elite)
            for g in elite:
                key = (g["white"], g["black"], g.get("date"), g["result"])
                if key not in dedup:
                    dedup.add(key)
                    all_games.append(g)
                    tour_games += 1
        print(f"  {tour_games} elite games from this tournament")
        print(f"  Running total: {len(all_games)} elite games")
    if asof_iso:
        print(f"\nAS-OF filter dropped {skipped_asof} games on/after {asof_iso}")

    print("\nComputing TPR for all players...")
    stats = build_player_stats(all_games)

    # Filter to players with enough data
    qualified = {
        name: data for name, data in stats.items()
        if data["elite_games"] >= MIN_GAMES_FOR_TPR
    }

    print(f"\nPlayers with TPR data: {len(qualified)}")
    print("Top 20 by TPR:")
    top20 = sorted(
        [(n, d) for n, d in qualified.items() if d["tpr"] is not None],
        key=lambda x: x[1]["tpr"],
        reverse=True,
    )[:20]
    for player, data in top20:
        dr_str = f"{data['draw_rate']:.2f}" if data["draw_rate"] is not None else " N/A"
        print(
            f"  {player:<30}  TPR {data['tpr']:>4.0f}  "
            f"draw {dr_str}  games {data['elite_games']:>3} "
            f"(w {data.get('weighted_games', 0):>5.1f})"
        )

    output = {
        "generated_at":        datetime.now().isoformat(),
        "tournaments_scanned": len(HARDCODED_ROUNDS),
        "total_elite_games":   len(all_games),
        "players":             qualified,
    }

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ {output_path} written with {len(qualified)} players")


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    from datetime import datetime
    ap = argparse.ArgumentParser(description="Build TPR/volatility data from PGN.")
    ap.add_argument("--asof", type=str, default=None,
                    help="ISO date 'YYYY-MM-DD'. Excludes games on/after this "
                         "date and uses it as the recency reference (out-of-sample).")
    ap.add_argument("--no-recency", dest="recency", action="store_false",
                    help="Disable recency weighting (all games equal weight, A/B baseline).")
    ap.add_argument("--out", type=str, default="tpr_data.json",
                    help="Output JSON path (default tpr_data.json)")
    ap.set_defaults(recency=True)
    args = ap.parse_args()
    run(asof=args.asof, recency=args.recency, output_path=args.out)
