"""Runs once manually to populate market_curator.py with validated exhaustive market sets discovered via the Polymarket Gamma API."""

import json
import os
import re

import requests


POLITICAL_KEYWORDS = {
    "senate", "governor", "congress", "election", "primary",
    "president", "mayor", "representative", "candidate", "vote",
    "democrat", "republican", "ballot", "midterm", "runoff",
}


def run():
    # ── STEP 1: Fetch all open events from Gamma API (paginated) ────────────
    print("Fetching open events from Gamma API...")
    all_events = {}
    offset = 0
    limit = 100

    while True:
        try:
            response = requests.get(
                "https://gamma-api.polymarket.com/events",
                params={"closed": "false", "limit": limit, "offset": offset},
            )
            response.raise_for_status()
            page = response.json()
            if not page:
                break
            for event in page:
                eid = event.get("id")
                if eid and eid not in all_events:
                    all_events[eid] = event
            if len(page) < limit:
                break
            offset += limit
        except Exception as e:
            print(f"  Error fetching events at offset {offset}: {e}")
            break

    print(f"{len(all_events)} unique open events found.\n")

    # ── STEP 2: Filter to valid exhaustive sets ──────────────────────────────
    # Keep events with 2-4 markets whose title contains a political keyword
    filtered = []
    for event in all_events.values():
        markets = event.get("markets", [])
        title = event.get("title", "").lower()
        n = len(markets)

        if not (2 <= n <= 4):
            continue
        if not any(word in title for word in POLITICAL_KEYWORDS):
            continue
        if not all(m.get("active", False) for m in markets):
            continue

        filtered.append(event)

    print(f"{len(filtered)} group(s) passed the exhaustive set filter.")
    for e in filtered:
        print(f"  [{len(e['markets'])} markets] {e['title']}  (slug: {e['slug']})")
    print()

    # ── STEP 3: Select top 5 sets ────────────────────────────────────────────
    filtered.sort(key=lambda e: len(e["markets"]), reverse=True)
    top_sets = filtered[:5]

    # ── STEP 4: Calculate dynamic threshold for each set ────────────────────
    for event in top_sets:
        n = len(event["markets"])
        event["threshold"] = round(1.0 - (0.02 * n), 2)

    # ── STEP 5: Write into market_curator.py ────────────────────────────────
    if len(top_sets) < 2:
        print(f"WARNING: Only {len(top_sets)} set(s) found — fewer than the recommended 2.")

    lines = [
        '"""Stores a hardcoded dictionary of curated exhaustive market sets targeting low-liquidity political markets on Polymarket."""\n',
        "\n",
        "EXHAUSTIVE_SETS = {\n",
    ]

    for event in top_sets:
        key = event["slug"].replace("-", "_")
        slugs = [m["slug"] for m in event["markets"]]
        threshold = event["threshold"]
        name = event["title"]
        slug_list = ", ".join(f'"{s}"' for s in slugs)
        lines.append(f'    "{key}": {{\n')
        lines.append(f'        "slugs": [{slug_list}],\n')
        lines.append(f'        "threshold": {threshold},\n')
        lines.append(f'        "name": "{name}",\n')
        lines.append(f'    }},\n')

    lines.append("}\n")
    lines.append("\n")

    curator_path = os.path.join(os.path.dirname(__file__), "market_curator.py")
    with open(curator_path, "w") as f:
        f.writelines(lines)

    print(f"✓ market_curator.py written with {len(top_sets)} set(s).\n")
    print("── Final market_curator.py ──")
    print("".join(lines))


if __name__ == "__main__":
    run()
