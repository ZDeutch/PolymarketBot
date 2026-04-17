# PolymarketBot

A chess tournament simulator that figures out whether Polymarket's betting
odds on a tournament winner are mispriced, and if so, how much to bet.

---

## What it does in one sentence

It runs 50,000 simulated versions of a chess tournament, counts how often
each player wins, and compares that to what Polymarket thinks. If the
numbers disagree by more than 8%, it tells you to bet — and exactly how
much.

---

## The pipeline (what happens when you run it)

```
main.py --tournament "TePe Sigeman 2026" --tour-id XXXXXXXX --polymarket "..."
```

```
Step 1  Fetch tournament info from Lichess
        → how many rounds, which are finished, who is playing

Step 2  Fetch completed game results from Lichess
        → who beat who in every finished round

Step 3  Build the player list
        → from Lichess round 1 data, or from POLYMARKET_SLUGS if not started yet

Step 4a Fetch player ratings from Chess.com
        → falls back to hardcoded FIDE ratings if Chess.com fails

Step 4b Build the remaining schedule
        → every game not yet played (single or double round-robin)

Step 5  Run 50,000 Monte Carlo simulations
        → simulate the rest of the tournament 50,000 times
        → record who wins each simulation

Step 6  Fetch live Polymarket prices
        → the YES price for each player to win

Step 7  Print the edge table
        → model% vs market%, flag bets with ★, show recommended stake
```

---

## File by file

### `config.py` — the dials

All the numbers you might want to tweak, in one place.

| Setting | Value | Meaning |
|---|---|---|
| `SIMULATIONS` | 50,000 | How many times to simulate the tournament |
| `KELLY_FRACTION` | 0.25 | Bet 25% of what pure Kelly says (conservative) |
| `MIN_EDGE` | 0.08 | Only bet if model disagrees with market by 8%+ |
| `MIN_MARKET_PRICE` | 0.05 | Ignore players the market prices below 5% |
| `MIN_STAKE` | $100 | Never recommend a bet smaller than $100 |
| `BANKROLL` | $10,000 | Your total pot |
| `MAX_TOURNAMENT_EXPOSURE` | 0.05 | Never risk more than 5% of bankroll on one tournament |

---

### `fetchers/chess_fetcher.py` — getting data from the internet

Handles all HTTP requests. Has three jobs:

**1. Get tournament info** (`get_tournament`)
Calls the Lichess broadcast API with the 8-character tour ID (e.g. `BLA70Vds`).
Returns: tournament name, how many rounds, which are finished, each round's ID.

**2. Get game results** (`get_completed_results`, `get_round_games`)
For every finished round, downloads the PGN file (standard chess game notation)
and parses it to extract: White player, Black player, result (1-0 / 0-1 / ½-½).
Converts results to scores: win=1.0, draw=0.5, loss=0.0.

**3. Get player ratings** (`get_player_ratings`)
For each player, tries in order:
1. Chess.com API (classical rating, must be above 2500 to count)
2. Hardcoded `KNOWN_RATINGS` table (sourced from FIDE April 2026 list)

`KNOWN_RATINGS` is the safety net. Every player in the Sigeman 2026 field
is in there with their current FIDE rating, so the bot never fails silently
with a wrong number.

**4. Build the remaining schedule** (`get_remaining_schedule`)
Generates every possible (White, Black) pair for the field, then removes
the games that have already been played. Handles both single round-robin
(each pair plays once, e.g. Sigeman) and double round-robin (each pair
plays twice, e.g. FIDE Candidates) by checking if `total_rounds == n - 1`.

**Name normalization**
PGN files write names as "Last, First" (e.g. "Carlsen, Magnus"). The code
flips this to "First Last" so names match everywhere. Special cases like
"Wei, Yi" and "Zhu, Jiner" (Chinese names that should stay surname-first)
are handled with explicit overrides.

---

### `simulators/chess_simulator.py` — the brain

This is where the 50,000 simulations happen.

**How one simulation works** (`simulate_tournament`)

1. For each player, sample a "performance rating" from a bell curve
   centered on their adjusted rating with σ=50 Elo. This captures the
   fact that players play stronger or weaker on any given day.

2. Seed the scores from real completed results (these are fixed facts,
   not simulated).

3. For each remaining game, call `simulate_game()` to get a result.

4. Find who has the most points. Random tiebreak if tied.

5. Repeat 50,000 times. Count wins per player. Divide by 50,000.
   That's your win probability.

**How one game is simulated** (`simulate_game`)

1. **White piece bonus**: White gets +35 Elo advantage (based on research
   across 266,000 games — white wins ~54% of decisive results).

2. **Win probability**: Standard Elo formula.
   `P(White wins) = 1 / (1 + 10^((Black_Elo - White_Elo) / 400))`

3. **Draw probability** (three layers):

   - *Kryukov baseline*: An exponential formula that captures how top
     players draw far more often than the Elo formula implies.
     `draw% = -|Δ|/32.49 + exp((average_rating - 2254.7) / 208.49) + 23.87`
     The exponential term means draws surge when average rating passes
     ~2700. This is why Carlsen vs Caruana draws 70%+ of the time.

   - *Per-player empirical rate*: From `tpr_data.json`, each player has
     a measured draw rate from their actual elite games. A player like
     Giri (historically 54% draws) is treated differently from Tal-style
     players who force complications. The Kryukov formula is used to
     scale this by the specific rating gap in the current game.

   - *Must-win adjustment*: If a player trails the leader by 2+ points
     with 4 or fewer games left, their draw probability drops by 15%.
     They're fighting for their tournament life and will take more risks.

4. **Result**: Random roll decides win/draw/loss using the above probabilities.

**Rating adjustment** (`get_adjusted_rating`)

Raw FIDE Elo is blended with each player's recent tournament performance
rating (TPR) from `tpr_data.json`. The blend formula:

- Full data (15+ elite games): 45% FIDE + 55% TPR
- Sparse data: the TPR weight scales down proportionally
- Velocity bonus: if recent TPR significantly exceeds FIDE rating
  (player on a hot streak), that difference adds 15% of itself to the
  adjusted rating
- Hard cap: the total adjustment is capped at ±50 Elo

This matters because FIDE ratings lag reality by months. A player who
just crushed a super-tournament is stronger than their FIDE number says.

---

### `tpr_data.json` — the player database

A pre-built file (not fetched at runtime). Stores per-player statistics
computed from ~6 elite tournaments (Tata Steel, Norway Chess, Grand Swiss,
FIDE Candidates, etc.).

For each player with enough data (5+ elite games):

```json
"Anish Giri": {
  "tpr":          2759.0,   ← tournament performance rating
  "draw_rate":    0.5385,   ← draws / total games (vs 2600+ opponents)
  "elite_games":  13,       ← games used to compute these numbers
  "avg_opponent": 2741.2    ← average opponent strength
}
```

Players not in this file (like Zhu Jiner) fall back to raw FIDE Elo
and the Kryukov draw formula. Their simulation is still valid, just
slightly less personalized.

---

### `tpr_builder.py` — the tool that builds tpr_data.json

Run this manually (not at runtime) to rebuild the player database.

What it does:
1. Downloads PGN files from Lichess for each hardcoded tournament
2. Filters to games where at least one player is rated 2600+
3. For each player, computes their max-likelihood TPR using a standard
   optimization (find the rating R that best explains their actual results)
4. Computes their personal draw rate (draws / games vs 2600+)
5. Writes everything to `tpr_data.json`

You add new tournament round IDs to `HARDCODED_ROUNDS` as tournaments
finish, then re-run to refresh the database.

**Run it:**
```
python3 tpr_builder.py
```

---

### `edge_calculator.py` — find and size the bets

**Finding edges** (`find_edges`)

For each player who has both a model probability and a Polymarket price:
- `edge = model_probability - market_price`
- If edge ≥ +8%: action = **YES** (buy YES, market is underpricing them)
- If edge ≤ −8%: action = **NO** (buy NO, market is overpricing them)
- Between −8% and +8%: action = **neutral** (no bet)

**Sizing bets** (`calculate_kelly`)

Uses the Kelly criterion — a formula from information theory that tells
you the mathematically optimal fraction of your bankroll to bet:

`full_kelly = (odds × win_probability - loss_probability) / odds`

For example: if the model says 40% but market says 25%, you're getting
3:1 odds on a 40% shot. Kelly says bet 20% of bankroll. But we use
quarter-Kelly (×0.25), so we'd bet 5% instead. This is more conservative
but much safer when model predictions are imperfect.

**Exposure cap** (`size_positions`)

After sizing all bets individually, if the total across all players in
the tournament exceeds 5% of bankroll ($500 on a $10k bankroll), every
bet is scaled down proportionally to hit exactly the cap.

Example: model says bet $600 total across 3 players. Cap is $500.
Scale factor = 500/600 = 0.83. Every stake multiplied by 0.83.

---

### `main.py` — the entry point

Ties everything together. Also contains:

**`POLYMARKET_SLUGS`** — a dict mapping player names to their Polymarket
market URL slugs. Update this for each new tournament. The slugs are the
part of the Polymarket URL after `/event/`, e.g.:
`will-magnus-carlsen-win-the-2026-tepe-sigeman-chess-tournament`

**Pre-tournament gate** — if more than 4 rounds are complete, the bot
exits with a warning. Polymarket prices adjust after each round; trying
to find edges mid-tournament is unreliable. Pass `--force` to override.

**Pre-tournament fallback** — if Lichess has no round 1 results yet (the
tournament hasn't started), the player list is built from `POLYMARKET_SLUGS`
keys instead. This lets you run the model before a single game is played.

---

## How to run it

```bash
# Pre-tournament (no games played yet)
python3 main.py \
  --tournament "TePe Sigeman 2026" \
  --tour-id XXXXXXXX \
  --polymarket "tepe-sigeman-2026" \
  --check

# During tournament (rounds 1-4)
python3 main.py \
  --tournament "TePe Sigeman 2026" \
  --tour-id XXXXXXXX \
  --polymarket "tepe-sigeman-2026"

# Mid-tournament research (rounds 5+, bypass gate)
python3 main.py \
  --tournament "TePe Sigeman 2026" \
  --tour-id XXXXXXXX \
  --polymarket "tepe-sigeman-2026" \
  --force
```

**Getting the tour ID:** Find the tournament on `lichess.org/broadcast`.
The ID is the 8-character code at the end of the URL, e.g.:
`lichess.org/broadcast/tepe-sigeman-2026/.../BLA70Vds` → ID is `BLA70Vds`

---

## Reading the output

```
────────────────────────────────────────────────────────────────────
  Player                   Model    Market     Edge  Action    Stake
────────────────────────────────────────────────────────────────────
  Magnus Carlsen           45.2%    38.0%    + 7.2%  neutral       —
  Nodirbek Abdusattorov    22.1%    10.0%   +12.1%  YES       $ 250 ★
  Arjun Erigaisi           15.3%    18.0%    - 2.7%  neutral       —
  Jorden van Foreest       10.4%    14.0%    - 9.6%  NO        $ 180 ★
  ...
────────────────────────────────────────────────────────────────────
```

- **Model**: what the 50,000 simulations say the probability is
- **Market**: what Polymarket is currently pricing
- **Edge**: model minus market (positive = market underprices them)
- **Action**: YES = buy YES, NO = buy NO, neutral = no bet
- **Stake**: recommended dollar amount (0 if neutral or below $100 minimum)
- **★**: this is an actionable bet

---

## Updating for a new tournament

1. Update `POLYMARKET_SLUGS` in `main.py` with the new players and their
   Polymarket market slugs
2. Check `KNOWN_RATINGS` in `chess_fetcher.py` — add any new players with
   their current FIDE rating
3. Add new name variants to `PLAYER_NAME_TO_USERNAME` if they're on Chess.com
4. Add new name normalization entries to `PGN_NAME_OVERRIDES` (chess_fetcher.py)
   and `_NAME_REPLACEMENTS` (tpr_builder.py) if needed
5. Add a new entry to `HARDCODED_ROUNDS` in `tpr_builder.py` and populate it
   with round IDs as the tournament progresses, then re-run `tpr_builder.py`
