# PolymarketBot

A chess tournament Monte Carlo simulator that finds mispriced betting
markets on Polymarket and Kalshi.

---

## What it does in one sentence

It runs 50,000 simulated versions of a chess tournament, counts how often
each player wins, and compares that to what the prediction markets think.
If the numbers disagree by more than 8%, it tells you to bet — and how much.

---

## Project structure

```
main.py                        ← entry point; run this
config.py                      ← all tunable constants in one place
edge_calculator.py             ← edge detection + Kelly bet sizing
freshness.py                   ← data staleness gating (blocks stale runs)
server.py                      ← local HTTP server for the dashboard
tpr_builder.py                 ← (manual) rebuilds tpr_data.json
tpr_data.json                  ← pre-built player performance database

fetchers/
  chess_fetcher.py             ← Lichess API + Chess.com ratings + FIDE fallback
  kalshi_fetcher.py            ← Kalshi market prices
  fide_fetcher.py              ← FIDE XML downloader for rating updates

simulators/
  chess_simulator.py           ← 50,000-iteration Monte Carlo engine

backtest/
  runner.py                    ← replays historical cases through the model
  cases.py                     ← historical tournament data with known winners
  metrics.py                   ← accuracy / calibration / Brier score

dashboard.html                 ← live dashboard UI
methodology.html               ← model methodology writeup

manifest.json / manifest.csv   ← index of the local elite PGN database
```

---

## The pipeline

```
python main.py --tournament "TePe Sigeman 2026" \
               --tour-id 1X75mYa7 \
               --polymarket "tepe-sigeman-2026"
```

```
Step 1  Fetch tournament structure from Lichess
        → rounds, which are finished, round IDs

Step 2  Fetch completed game results from Lichess
        → who beat who in every finished round

Step 3  Build the player list
        → from Lichess round 1 data, or from POLYMARKET_SLUGS if pre-tournament

Step 4a Fetch player ratings
        → Chess.com API first, falls back to KNOWN_RATINGS (FIDE May 2026 list)

Step 4b Build the remaining schedule
        → all unplayed games (single or double round-robin auto-detected)

Step 5  Run 50,000 Monte Carlo simulations
        → sample performance ratings, simulate remaining games, count wins

Step 6  Fetch live market prices
        → Polymarket (via slug) + optionally Kalshi

Step 7  Print the edge table
        → model% vs market%, flag bets with ★, show recommended stake
```

---

## File by file

### `config.py` — the dials

| Setting | Value | Meaning |
|---|---|---|
| `SIMULATIONS` | 50,000 | Monte Carlo iterations |
| `KELLY_FRACTION` | 0.25 | Quarter-Kelly (conservative) |
| `MIN_EDGE` | 0.08 | Only bet at 8%+ model-market disagreement |
| `MIN_MARKET_PRICE` | 0.05 | Ignore sub-5% market prices |
| `MIN_STAKE` | $100 | Minimum recommended bet |
| `BANKROLL` | $10,000 | Total bankroll |
| `MAX_TOURNAMENT_EXPOSURE` | 0.05 | Cap total risk at 5% of bankroll per tournament |

---

### `fetchers/chess_fetcher.py` — data from Lichess + Chess.com

**Tournament info** (`get_tournament`): calls Lichess broadcast API with the
8-char tour ID. Returns name, round count, finished rounds, round IDs.

**Game results** (`get_completed_results`, `get_round_games`): downloads PGN
for each finished round, parses White/Black/result into score floats.

**Player ratings** (`get_player_ratings`): tries Chess.com API first (classical
rating ≥ 2500 required), falls back to `KNOWN_RATINGS` — hardcoded from the
FIDE May 2026 official list. Run `fetchers/fide_fetcher.py` to generate
updated diffs when a new FIDE list is released.

**Remaining schedule** (`get_remaining_schedule`): generates every unplayed
game. Auto-detects double round-robin when `total_rounds == n_players - 1`.

**Name normalization**: PGN writes "Last, First" — flipped to "First Last".
Special cases handled with explicit overrides (Wei Yi, Zhu Jiner, etc.).

---

### `fetchers/fide_fetcher.py` — FIDE rating updater

Downloads the official FIDE monthly XML rating lists (classical, rapid, blitz)
and diffs them against the hardcoded `KNOWN_RATINGS` in `chess_fetcher.py`.

```bash
# Show what changed for classical ratings
python fetchers/fide_fetcher.py --fmt classical --diff

# Download all three formats at once
python fetchers/fide_fetcher.py --fmt all
```

Output is copy-paste–ready Python dicts. Run once per month after FIDE releases
a new list, update `KNOWN_RATINGS` / `KNOWN_RATINGS_RAPID` / `KNOWN_RATINGS_BLITZ`.

---

### `fetchers/kalshi_fetcher.py` — Kalshi market prices

Fetches YES prices from Kalshi for tournaments that have Kalshi markets.
Called from `main.py` alongside the Polymarket fetch.

---

### `simulators/chess_simulator.py` — the Monte Carlo engine

**One simulation** (`simulate_tournament`):
1. Sample each player's performance rating from N(adjusted_rating, σ)
2. Seed actual completed scores (fixed facts)
3. Simulate remaining games via `simulate_game()`
4. Find the winner; random tiebreak if tied

**One game** (`simulate_game`):
1. White piece bonus: +35 Elo (empirical from 266k games)
2. Win probability: standard Elo formula
3. Draw probability — three layers:
   - *Kryukov baseline*: exponential formula calibrated on super-GM data;
     draws surge past ~2700 average rating
   - *Per-player empirical rate*: from `tpr_data.json`, each player's
     measured draw rate vs 2600+ opposition (e.g. Giri draws 54%)
   - *Must-win adjustment*: player trailing by 2+ points with ≤4 rounds
     left has draw probability reduced 15%

**Rating adjustment** (`get_adjusted_rating`):
Blends FIDE Elo with each player's TPR from `tpr_data.json`:
- Full data (15+ elite games): 45% FIDE + 55% TPR
- Sparse data: TPR weight scales down proportionally
- Velocity bonus: if TPR significantly exceeds FIDE, 15% of the gap is
  added (catches players on a hot streak that FIDE hasn't caught up to yet)
- Hard cap: ±50 Elo max adjustment

---

### `tpr_data.json` — the player database

Pre-built offline. Stores per-player stats computed from elite tournament games.

```json
"Anish Giri": {
  "tpr":          2759.0,   ← max-likelihood tournament performance rating
  "draw_rate":    0.5385,   ← weighted draws / games vs 2600+ opposition
  "volatility":   42.3,     ← per-event performance σ (used as simulation noise)
  "elite_games":  65,       ← games in the dataset
  "weighted_games": 38.2    ← recency-weighted game count
}
```

Players not in this file fall back to raw FIDE Elo + Kryukov draw formula.

---

### `tpr_builder.py` — rebuilds tpr_data.json

Run manually (not at runtime) to refresh the player database.

**Data sources** (in priority order):
1. **Local PGN cache** (`elite_games_2020_present.pgn`) — 6,500+ elite games
   from 42 tournaments (Nov 2021–May 2026), loaded instantly with no network.
   Covers Tata Steel 2024/2025, Candidates 2024, Norway Chess 2024/2025,
   Grand Swiss 2025, WCC 2021/2023, Sinquefield Cup 2022, and more.
2. **Lichess API** — fetches any remaining tournaments not in the local PGN
   (Candidates 2026, Sigeman 2026, etc.). Deduplication prevents double-counting
   if a game appears in both sources.

**Recency weighting**: each game is weighted `exp(-age_days / 365)`.
Games from 1 year ago get weight 0.37; from 2 years ago, 0.14.

**TPR algorithm**: max-likelihood Elo estimator — finds the rating R that
maximizes `Σ [s·log(E(R,r)) + (1-s)·log(1-E(R,r))]` for each player's
results. Uses scipy if available; binary search fallback otherwise.

```bash
python tpr_builder.py                       # rebuild with today as reference date
python tpr_builder.py --asof 2025-01-01    # out-of-sample (for backtesting)
python tpr_builder.py --no-recency         # A/B: flat weights
```

**Adding new tournaments**: either add round IDs to `HARDCODED_ROUNDS`, or
register the tour ID in the freshness ledger:
```bash
python -m freshness mark tour_id "Tournament Name" --value <8-char-id>
```
Both methods are auto-merged at build time.

---

### `freshness.py` — staleness gating

Tracks when data was last fetched and blocks `main.py` from running on stale data.

| Gate | Max age | Description |
|---|---|---|
| `tpr_data.json` | 24 hours | Warns if database is old |
| Kalshi prices | 2 hours | Hard block if prices are stale |
| `tour_id` | required | Hard block if tour ID not registered |

```bash
python freshness.py status                              # show all gates
python -m freshness mark tour_id "Name" --value <id>   # register a tournament
```

---

### `edge_calculator.py` — finding and sizing bets

**Edge detection** (`find_edges`):
- `edge = model_probability − market_price`
- `edge ≥ +8%` → YES (buy YES, market underprices)
- `edge ≤ −8%` → NO (buy NO, market overprices)

**Kelly sizing** (`calculate_kelly`):
`full_kelly = (odds × win_prob − loss_prob) / odds`
Applied at quarter-Kelly, then capped at 5% total tournament exposure.

---

### `server.py` — local dashboard server

Serves `dashboard.html` and `methodology.html` on localhost.

```bash
python server.py        # starts on http://localhost:8080
```

---

### `backtest/` — model validation

Replays historical tournaments through the simulator to measure accuracy.

```bash
python -m backtest.runner                              # all cases, in-sample (fast)
python -m backtest.runner --oos                        # true out-of-sample (rebuilds TPR per case)
python -m backtest.runner --case "Norway Chess 2024"   # single case
```

Metrics reported: winner accuracy, top-3 accuracy, Brier score, calibration curve.

---

## Reading the output

```
─────────────────────────────────────────────────────────────────────────
  Player                   Model    Market     Edge  Action    Stake
─────────────────────────────────────────────────────────────────────────
  Magnus Carlsen           45.2%    38.0%    + 7.2%  neutral       —
  Nodirbek Abdusattorov    22.1%    10.0%   +12.1%  YES       $ 250 ★
  Arjun Erigaisi           15.3%    18.0%    - 2.7%  neutral       —
  Jorden van Foreest       10.4%    14.0%    - 9.6%  NO        $ 180 ★
─────────────────────────────────────────────────────────────────────────
```

- **Model**: win probability from the 50,000 simulations
- **Market**: current Polymarket YES price
- **Edge**: model minus market (positive = market underprices)
- **Action**: YES = buy YES contracts, NO = buy NO contracts
- **Stake**: recommended dollar amount
- **★**: actionable bet

---

## How to run

```bash
# Pre-tournament (no games played yet)
python main.py \
  --tournament "TePe Sigeman 2026" \
  --tour-id 1X75mYa7 \
  --polymarket "tepe-sigeman-2026"

# During tournament (rounds 1–4)
python main.py \
  --tournament "TePe Sigeman 2026" \
  --tour-id 1X75mYa7 \
  --polymarket "tepe-sigeman-2026"

# Mid-tournament research (rounds 5+, bypass the round-count gate)
python main.py \
  --tournament "TePe Sigeman 2026" \
  --tour-id 1X75mYa7 \
  --polymarket "tepe-sigeman-2026" \
  --force
```

**Finding the tour ID**: go to `lichess.org/broadcast`, find the tournament.
The 8-character tour ID is at the end of the broadcast URL:
`lichess.org/broadcast/tepe-sigeman-2026/.../1X75mYa7` → `1X75mYa7`

---

## Setting up a new tournament

1. Update `POLYMARKET_SLUGS` in `main.py` with player names and their
   Polymarket market URL slugs.
2. Check `KNOWN_RATINGS` in `chess_fetcher.py` — add any new players with
   their current FIDE rating.
3. Add new name variants to `PLAYER_NAME_TO_USERNAME` if they're on Chess.com,
   and to `_NAME_REPLACEMENTS` in `tpr_builder.py` if their PGN name is unusual.
4. Register the tour ID for freshness tracking:
   ```bash
   python -m freshness mark tour_id "Tournament Name" --value <8-char-id>
   ```
   This also causes `tpr_builder.py` to auto-fetch that tournament's rounds.

---

## Updating ratings (monthly)

When FIDE releases a new rating list:
```bash
python fetchers/fide_fetcher.py --fmt all --diff
```
Copy the printed output into `KNOWN_RATINGS` / `KNOWN_RATINGS_RAPID` /
`KNOWN_RATINGS_BLITZ` in `fetchers/chess_fetcher.py`.
