"""
Backtest cases — historical tournaments with known winners.

Each case captures the minimum needed to replay the model:
  - tournament name
  - list of players (canonical names)
  - pre-tournament FIDE ratings
  - total rounds (for double round-robin detection)
  - actual winner(s) — list, since some tournaments have ties
  - optional: market_prices = {player: yes_price} from the closing market,
    if available. None disables the P&L / log-loss-vs-market metrics.

Add new cases here as historical price data becomes available.
"""

from dataclasses import dataclass, field


@dataclass
class BacktestCase:
    name:           str
    players:        list[str]
    fide_ratings:   dict[str, int]
    total_rounds:   int
    actual_winners: list[str]            # canonical names; >1 if joint winners
    market_prices:  dict[str, float] | None = None   # {player: yes_close}
    start_date:     str | None = None    # ISO 'YYYY-MM-DD' for out-of-sample asof


# ─────────────────────────────────────────────────────────────────────────────
# Historical cases. Pre-tournament ratings are FIDE published ratings as of
# the tournament start. Add closing market prices when available.
# ─────────────────────────────────────────────────────────────────────────────

CASES: list[BacktestCase] = [
    # Norway Chess 2024 — Carlsen won
    BacktestCase(
        name="Norway Chess 2024",
        players=[
            "Magnus Carlsen", "Hikaru Nakamura", "Fabiano Caruana",
            "Alireza Firouzja", "Praggnanandhaa R", "Ding Liren",
        ],
        fide_ratings={
            "Magnus Carlsen":    2830,
            "Hikaru Nakamura":   2794,
            "Fabiano Caruana":   2805,
            "Alireza Firouzja":  2760,
            "Praggnanandhaa R":  2747,
            "Ding Liren":        2762,
        },
        total_rounds=10,        # double round-robin
        actual_winners=["Magnus Carlsen"],
        market_prices=None,     # add if recoverable
        start_date="2024-05-26",
    ),

    # Tata Steel 2025 — Gukesh won (joint with Pragg, won tiebreak)
    BacktestCase(
        name="Tata Steel 2025",
        players=[
            "Gukesh D", "Praggnanandhaa R", "Fabiano Caruana", "Anish Giri",
            "Wei Yi", "Vincent Keymer", "Nodirbek Abdusattorov", "Arjun Erigaisi",
            "Pentala Harikrishna", "Max Warmerdam",
            "Vladimir Fedoseev", "Leon Luke Mendonca", "Jorden van Foreest",
            "Daniil Dubov",
        ],
        fide_ratings={
            "Gukesh D":               2783,
            "Praggnanandhaa R":       2758,
            "Fabiano Caruana":        2803,
            "Anish Giri":             2749,
            "Wei Yi":                 2760,
            "Vincent Keymer":         2737,
            "Nodirbek Abdusattorov":  2767,
            "Arjun Erigaisi":         2801,
            "Pentala Harikrishna":    2696,
            "Max Warmerdam":          2632,
            "Vladimir Fedoseev":      2705,
            "Leon Luke Mendonca":     2613,
            "Jorden van Foreest":     2671,
            "Daniil Dubov":           2641,
        },
        total_rounds=13,        # single round-robin (n-1 rounds)
        actual_winners=["Gukesh D"],   # tiebreak winner
        market_prices=None,
        start_date="2025-01-18",
    ),

    # Candidates 2024 — Gukesh won
    BacktestCase(
        name="Candidates 2024",
        players=[
            "Gukesh D", "Fabiano Caruana", "Ian Nepomniachtchi",
            "Praggnanandhaa R", "Hikaru Nakamura", "Vidit Gujrathi",
            "Alireza Firouzja", "Nijat Abasov",
        ],
        fide_ratings={
            "Gukesh D":               2747,
            "Fabiano Caruana":        2803,
            "Ian Nepomniachtchi":     2758,
            "Praggnanandhaa R":       2747,
            "Hikaru Nakamura":        2789,
            "Vidit Gujrathi":         2727,
            "Alireza Firouzja":       2760,
            "Nijat Abasov":           2632,
        },
        total_rounds=14,        # double round-robin (2*(n-1))
        actual_winners=["Gukesh D"],
        market_prices=None,
        start_date="2024-04-04",
    ),
]
