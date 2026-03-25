"""Handles all Polymarket API communication. Resolves market slugs to token IDs via the Gamma API and fetches live midpoint prices via the CLOB API."""

import json
import requests


def get_token_ids(slug: str) -> dict:
    url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        outcomes = json.loads(data["outcomes"])
        token_ids = json.loads(data["clobTokenIds"])
        return dict(zip(outcomes, token_ids))
    except Exception as e:
        print(f"Error fetching token IDs for '{slug}': {e}")
        return {}


def get_midpoint_prices(token_ids: list) -> dict:
    prices = {}
    for token_id in token_ids:
        try:
            response = requests.get(
                "https://clob.polymarket.com/midpoint",
                params={"token_id": token_id}
            )
            response.raise_for_status()
            prices[token_id] = float(response.json()["mid"])
        except Exception as e:
            print(f"Error fetching midpoint price for token {token_id}: {e}")
    return prices


def get_prices_for_market(slug: str) -> dict:
    outcome_to_token = get_token_ids(slug)
    if not outcome_to_token:
        return {}

    prices = get_midpoint_prices(list(outcome_to_token.values()))
    if not prices:
        return {}

    return {outcome: prices[token_id] for outcome, token_id in outcome_to_token.items() if token_id in prices}


if __name__ == "__main__":
    result = get_prices_for_market("will-there-be-a-us-recession-in-2025")
    print(result)
