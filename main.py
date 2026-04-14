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
