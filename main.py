"""
PolymarketBot — FIDE Candidates 2026 Elo Model

Entry point for the Elo-based probability model.
Scrapes live FIDE ratings, runs Monte Carlo simulation
of the remaining tournament, fetches live Polymarket
prices, and prints a ranked edge table showing where
the model disagrees with the market by more than 5%.

Usage: python main.py
"""

from elo_model import run

if __name__ == "__main__":
    run()
