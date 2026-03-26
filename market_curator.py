"""Stores a hardcoded dictionary of curated exhaustive market sets targeting low-liquidity political markets on Polymarket."""

EXHAUSTIVE_SETS = {
    "german_election_2025": {
        "slugs": [
            "will-the-afd-win-the-most-seats-in-the-2025-german-federal-election",
            "will-the-cdu-csu-win-the-most-seats-in-the-2025-german-federal-election",
            "will-the-spd-win-the-most-seats-in-the-2025-german-federal-election"
        ],
        "threshold": 0.94,
        "name": "German Federal Election 2025 — most seats"
    },
    "france_president_2027": {
        "slugs": [
            "will-marine-le-pen-win-the-2027-french-presidential-election",
            "will-emmanuel-macron-win-the-2027-french-presidential-election",
            "will-someone-else-win-the-2027-french-presidential-election"
        ],
        "threshold": 0.94,
        "name": "French Presidential Election 2027"
    },
}

SYNTHETIC_PRICES = {
    "german_election_2025": {
        "AFD wins most seats": 0.40,
        "CDU/CSU wins most seats": 0.35,
        "SPD wins most seats": 0.15
    },
    "france_president_2027": {
        "Le Pen wins": 0.42,
        "Macron wins": 0.38,
        "Other wins": 0.10
    },
}

TEST_MODE = True
