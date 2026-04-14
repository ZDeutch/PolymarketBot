# PolymarketBot

An Elo-based Monte Carlo simulator for the FIDE Candidates 2026 tournament that compares model probabilities to live Polymarket prices to identify edges. Also includes a live market scanner that monitors all active Polymarket exhaustive sets for sum-to-one arbitrage opportunities.

## Modules

| File | Description |
|------|-------------|
| `main.py` | Entry point. Runs the Elo model. |
| `elo_model.py` | Scrapes FIDE ratings, runs 50k Monte Carlo simulations, fetches live Polymarket prices, outputs edge table. |
| `live_scanner.py` | Scans all 10k+ Polymarket events every 2 minutes for exhaustive sets where sum of prices falls below fee-adjusted threshold. |
| `graph.py` | Builds weighted directed graph and runs modified Bellman-Ford to detect arbitrage below dynamic threshold. |
| `position_sizer.py` | Calculates optimal stake per outcome normalized to $10k simulated bankroll. |
| `sheets_logger.py` | Logs confirmed opportunities to Google Sheets with timestamp, price, stake, and P/L. |
| `visualizer.py` | Reads from Google Sheets and generates static matplotlib P/L chart. |
| `polymarket_client.py` | Resolves market slugs to token IDs via Gamma API and fetches live midpoint prices via CLOB API. |

## Usage

Run the Elo model:
```
python main.py
```

Run the live arbitrage scanner:
```
python live_scanner.py
```

Generate P/L chart from logged sessions:
```
python visualizer.py
```

## Multi-Sport Architecture (In Progress)

PolymarketBot is being extended to support
three tournament formats:

| Sport  | Rating System | Format            | Data Source      |
|--------|---------------|-------------------|------------------|
| Chess  | FIDE Elo      | Round robin       | Chess.com API    |
| Tennis | UTR           | Knockout bracket  | UTR Sports API   |
| Golf   | OWGR/DataGolf | Stroke play       | DataGolf API     |

Each sport has a dedicated fetcher (data) and
simulator (Monte Carlo engine). The edge
calculator and paper trader are sport-agnostic.

### Usage
```
python main.py --tournament "..." \
               --sport [chess|tennis|golf] \
               --polymarket "event-slug"
```

## Time Complexity

- `polymarket_client.get_prices_for_market()`: O(n) where n = number of outcomes
- `graph.detect_arbitrage()`: O(V²E) Bellman-Ford where V = outcomes, E = edges in fully connected graph
- `position_sizer.calculate_stakes()`: O(n) where n = number of outcomes
- `elo_model.run_simulation()`: O(S × G) where S = simulations (50k), G = remaining games (48)
- `live_scanner` fetch cycle: O(E × M) where E = valid events (~110), M = markets per event
