"""Standalone scanner: fetches all open Polymarket events, identifies exhaustive sets,
fetches live prices, and ranks by arb opportunity."""

import requests
import json
import time
from datetime import datetime


def slug_to_name(slug: str) -> str:
    parts = slug.split("-")
    if parts[0] == "will":
        parts = parts[1:]
    return " ".join(word.capitalize() for word in parts[:2])


def fetch_all_events():
    all_events = []
    seen_ids = set()
    offset = 0
    limit = 100

    while True:
        try:
            response = requests.get(
                "https://gamma-api.polymarket.com/events",
                params={"closed": "false", "limit": limit, "offset": offset},
                timeout=15,
            )
            response.raise_for_status()
            page = response.json()
        except Exception as e:
            print(f"Error fetching events at offset {offset}: {e}")
            break

        if not page:
            break

        for event in page:
            eid = event.get("id")
            if eid not in seen_ids:
                seen_ids.add(eid)
                all_events.append(event)

        print(f"Fetched {offset + len(page)} events...")

        if len(page) < limit:
            break
        offset += limit

    return all_events


def filter_exhaustive_sets(events):
    valid = []

    for event in events:
        markets = event.get("markets", [])
        n = len(markets)

        if not (2 <= n <= 8):
            continue
        if event.get("closed", False):
            continue
        if not all(m.get("active", False) for m in markets):
            continue

        valid.append(event)

    return valid


def fetch_prices_for_event(event):
    """Extracts Yes prices from outcomePrices already embedded in the Gamma event data.
    No additional API calls needed — all data is in hand from fetch_all_events().
    Falls back to full slug as key if slug_to_name produces a collision."""
    prices = {}

    for market in event["markets"]:
        slug = market.get("slug")
        if not slug:
            return {}

        try:
            outcomes = json.loads(market.get("outcomes", "[]"))
            outcome_prices = json.loads(market.get("outcomePrices", "[]"))
        except (json.JSONDecodeError, TypeError):
            return {}

        if not outcomes or not outcome_prices or len(outcomes) != len(outcome_prices):
            return {}

        price_map = dict(zip(outcomes, outcome_prices))
        yes_str = price_map.get("Yes")
        if yes_str is None:
            return {}

        try:
            yes_price = float(yes_str)
        except (ValueError, TypeError):
            return {}

        name = slug_to_name(slug)
        if name in prices:
            # Collision: two markets in this event share the same short name (e.g. different
            # deadline variants of the same question). Not a true exhaustive set — skip.
            return {}
        prices[name] = yes_price

    # Require at least 2 distinct priced outcomes
    if len(prices) < 2:
        return {}

    return prices


def score_event(event, prices):
    n = len(prices)
    cycle_sum = round(sum(prices.values()), 4)
    threshold = round(1.0 - (0.02 * n), 2)
    edge = round(threshold - cycle_sum, 4)
    return {
        "name": event.get("title", event.get("slug", "unknown")),
        "slug": event.get("slug", ""),
        "n": n,
        "cycle_sum": cycle_sum,
        "threshold": threshold,
        "edge": edge,
        "opportunity": edge >= 0,
        "prices": prices,
    }


def run():
    print("PolymarketBot Scanner")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print("Fetching all open events...")

    events = fetch_all_events()
    print(f"Total events fetched: {len(events)}")

    valid = filter_exhaustive_sets(events)
    print(f"Valid exhaustive sets: {len(valid)}")
    print("Scoring sets using inline Gamma prices (fast — no CLOB calls)...")
    print("")

    # Genuine exhaustive sets sum near 1.0. A sum below 0.5 almost always means
    # stale/zero prices or a non-exclusive "which of these can happen" market.
    MIN_CYCLE_SUM = 0.5

    scored = []
    for i, event in enumerate(valid):
        prices = fetch_prices_for_event(event)
        if not prices:
            continue

        result = score_event(event, prices)
        if result["cycle_sum"] < MIN_CYCLE_SUM:
            continue
        scored.append(result)

        if (i + 1) % 25 == 0:
            hits = sum(1 for s in scored if s["opportunity"])
            print(f"  Progress: {i+1}/{len(valid)} sets priced... ({hits} arb hits so far)")

    scored.sort(key=lambda x: x["edge"], reverse=True)

    opportunities = [s for s in scored if s["opportunity"]]
    print("")
    print("── GENUINE ARB OPPORTUNITIES " + "─" * 40)
    if opportunities:
        for i, s in enumerate(opportunities):
            print(
                f"  {i+1}. {s['name']:<45} "
                f"sum={s['cycle_sum']:.4f}  "
                f"threshold={s['threshold']}  "
                f"edge=+{s['edge']:.4f}  ← HIT"
            )
            for name, price in s["prices"].items():
                print(f"       {name}: {price}")
    else:
        print("  None found at current prices.")

    print("")
    print("── TOP 20 CLOSEST TO ARB " + "─" * 40)
    non_opps = [s for s in scored if not s["opportunity"]]
    for i, s in enumerate(non_opps[:20]):
        print(
            f"  {i+1}. {s['name']:<45} "
            f"sum={s['cycle_sum']:.4f}  "
            f"threshold={s['threshold']}  "
            f"edge={s['edge']:.4f}"
        )

    print("")
    print("── SUMMARY " + "─" * 40)
    print(f"  Genuine arb opportunities: {len(opportunities)}")
    within_5 = sum(1 for s in scored if -0.05 <= s["edge"] < 0)
    print(f"  Sets within 0.05 of threshold: {within_5}")
    print(f"  Total sets scored: {len(scored)}")


if __name__ == "__main__":
    run()
