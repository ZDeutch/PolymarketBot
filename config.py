"""
Central configuration file for PolymarketBot.

Loads all API keys and shared constants from .env.
Import this module anywhere you need API credentials
or system-wide settings.

Environment variables required:
  DATAGOLF_API_KEY     - DataGolf API key (free tier)
  UTR_JWT_TOKEN        - UTR Sports JWT token (from browser cookie)
  CHESS_COM_USER_AGENT - Chess.com API user agent string
  GOOGLE_SHEET_NAME    - Name of the Google Sheet for logging

Constants:
  SIMULATIONS          - Number of Monte Carlo iterations (50000)
  KELLY_FRACTION       - Fractional Kelly sizing (0.5)
  MIN_EDGE             - Minimum edge to log a position (0.05)
  MIN_MARKET_PRICE     - Minimum market price to consider (0.05)
  MIN_STAKE            - Minimum stake in dollars (100.0)
  BANKROLL             - Simulated bankroll in dollars (10000.0)
"""

from dotenv import load_dotenv
import os

load_dotenv()

# ─── API credentials ──────────────────────────────────────────────────────────

DATAGOLF_API_KEY     = os.getenv("DATAGOLF_API_KEY", "")
UTR_JWT_TOKEN        = os.getenv("UTR_JWT_TOKEN", "")
CHESS_COM_USER_AGENT = os.getenv(
    "CHESS_COM_USER_AGENT",
    "PolymarketBot/1.0 tournament-simulator"
)
GOOGLE_SHEET_NAME    = os.getenv("GOOGLE_SHEET_NAME", "ArbBotLog")

# ─── Simulation constants ─────────────────────────────────────────────────────

SIMULATIONS      = 50000
KELLY_FRACTION   = 0.5
MIN_EDGE         = 0.05
MIN_MARKET_PRICE = 0.05
MIN_STAKE        = 100.0
BANKROLL         = 10000.0
