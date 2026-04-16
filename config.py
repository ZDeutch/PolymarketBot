"""
Central configuration file for PolymarketBot.

Loads all API keys and shared constants from .env.
Import this module anywhere you need API credentials
or system-wide settings.

Environment variables:
  CHESS_COM_USER_AGENT - Chess.com API user agent string
  GOOGLE_SHEET_NAME    - Name of the Google Sheet for logging

Constants:
  SIMULATIONS            - Number of Monte Carlo iterations (50000)
  KELLY_FRACTION         - Fractional Kelly sizing (0.25 = quarter-Kelly)
  MIN_EDGE               - Minimum edge to act on (0.08 = 8%)
  MIN_MARKET_PRICE       - Minimum market price to consider (0.05)
  MIN_STAKE              - Minimum stake in dollars (100.0)
  BANKROLL               - Simulated bankroll in dollars (10000.0)
  MAX_TOURNAMENT_EXPOSURE- Max fraction of bankroll in one tournament (0.05)
"""

from dotenv import load_dotenv
import os

load_dotenv()

# ─── API credentials ──────────────────────────────────────────────────────────

CHESS_COM_USER_AGENT = os.getenv(
    "CHESS_COM_USER_AGENT",
    "PolymarketBot/1.0 tournament-simulator"
)
GOOGLE_SHEET_NAME    = os.getenv("GOOGLE_SHEET_NAME", "ArbBotLog")

# ─── Simulation constants ─────────────────────────────────────────────────────

SIMULATIONS             = 50000
KELLY_FRACTION          = 0.25   # quarter-Kelly — conservative pre-tournament sizing
MIN_EDGE                = 0.08   # 8% edge floor — 3% calibration buffer above 5% EV
MIN_MARKET_PRICE        = 0.05
MIN_STAKE               = 100.0
BANKROLL                = 10000.0
MAX_TOURNAMENT_EXPOSURE = 0.05   # total positions ≤ 5% of bankroll per tournament
