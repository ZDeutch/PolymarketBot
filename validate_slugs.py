"""Temporary script to validate all slugs in EXHAUSTIVE_SETS against the live Polymarket API."""

from market_curator import EXHAUSTIVE_SETS
from polymarket_client import get_prices_for_market

for set_name, set_data in EXHAUSTIVE_SETS.items():
    print(f"{set_data['name']}")
    for slug in set_data["slugs"]:
        result = get_prices_for_market(slug)
        if not result:
            print(f"  FAIL: {slug}")
        else:
            print(f"  OK:   {slug} → {result}")
    print()
