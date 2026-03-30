"""Stores a hardcoded dictionary of curated exhaustive market sets targeting low-liquidity political markets on Polymarket."""

EXHAUSTIVE_SETS = {
    "sc_senate_republican_primary": {
        "slugs": [
            "will-lindsey-graham-be-the-republican-nominee-for-senate-in-south-carolina",
            "will-paul-dans-be-the-republican-nominee-for-senate-in-south-carolina",
            "will-mark-lynch-be-the-republican-nominee-for-senate-in-south-carolina",
            "will-thomas-murphy-be-the-republican-nominee-for-senate-in-south-carolina",
        ],
        "threshold": 0.92,
        "name": "SC Senate Republican Primary",
    },
    "az_05_republican_primary": {
        "slugs": [
            "will-mark-lamb-be-the-republican-nominee-for-az-05",
            "will-travis-grantham-be-the-republican-nominee-for-az-05",
            "will-jay-feely-be-the-republican-nominee-for-az-05",
        ],
        "threshold": 0.94,
        "name": "AZ-05 Republican Primary",
    },
    "fide_candidates_2026": {
        "slugs": [
            "will-fabiano-caruana-win-the-2026-fide-candidates-tournament",
            "will-hikaru-nakamura-win-the-2026-fide-candidates-tournament",
            "will-javokhir-sindarov-win-the-2026-fide-candidates-tournament",
            "will-praggnanandhaa-r-win-the-2026-fide-candidates-tournament",
            "will-anish-giri-win-the-2026-fide-candidates-tournament",
            "will-wei-yi-win-the-2026-fide-candidates-tournament",
            "will-andrey-esipenko-win-the-2026-fide-candidates-tournament",
        ],
        "threshold": 0.86,
        "name": "FIDE Candidates Tournament 2026",
    },
}

SYNTHETIC_PRICES = {
    "sc_senate_republican_primary": {
        "Lindsey Graham": 0.70,
        "Paul Dans": 0.08,
        "Mark Lynch": 0.06,
        "Thomas Murphy": 0.03,
    },
    "az_05_republican_primary": {
        "Mark Lamb": 0.75,
        "Travis Grantham": 0.06,
        "Jay Feely": 0.05,
    },
    "fide_candidates_2026": {
        "Fabiano Caruana": 0.24,
        "Hikaru Nakamura": 0.17,
        "Javokhir Sindarov": 0.11,
        "Praggnanandhaa R": 0.10,
        "Anish Giri": 0.07,
        "Wei Yi": 0.07,
        "Andrey Esipenko": 0.02,
    },
}

TEST_MODE = False
