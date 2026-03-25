# PolymarketBot

PolymarketBot is a simulated arbitrage detection engine for Polymarket prediction markets. It polls live midpoint prices from the Polymarket CLOB API every 5 seconds across a curated set of exhaustive political markets, builds a weighted directed graph of outcomes, and runs a modified Bellman-Ford algorithm to identify mispricings where the sum of complementary outcome prices falls below 1.0 minus the fee threshold. When an opportunity is found, the bot calculates optimal position sizes against a $10,000 simulated bankroll, logs every trade to Google Sheets with full P&L tracking, and generates a matplotlib equity curve at session end — all without placing real money on the line.

## Modules

| File | Description |
|------|-------------|
| `polymarket_client.py` | Handles all Polymarket API communication, resolving slugs to token IDs and fetching live prices. |
| `market_curator.py` | Stores a hardcoded dictionary of curated exhaustive market sets targeting low-liquidity political markets. |
| `graph.py` | Builds a weighted directed graph from live prices and runs Bellman-Ford to detect fee-adjusted arbitrage. |
| `position_sizer.py` | Calculates optimal stake per outcome for a confirmed arbitrage opportunity against a $10,000 bankroll. |
| `sheets_logger.py` | Authenticates with Google Sheets and logs each simulated position with timestamp, price, stake, and P&L. |
| `visualizer.py` | Reads logged positions from Google Sheets and generates a static matplotlib P&L chart at session end. |
| `main.py` | Orchestrates the 5-second polling loop, wiring all modules from market discovery through position logging. |
