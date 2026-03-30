"""Continuously scans ALL open Polymarket events for exhaustive sets and logs
genuine arb opportunities to Google Sheets. Replaces hardcoded market_curator.py."""

import requests
import json
import time
import asyncio
import aiohttp
from datetime import datetime
from sheets_logger import get_sheet, log_opportunity
from position_sizer import calculate_stakes

# ─── Constants ───────────────────────────────────────────────────────────────

BANKROLL = 10000.0
SCAN_INTERVAL = 120       # seconds between full scans
COOLDOWN = 3600           # seconds before re-logging same set
MAX_MARKETS_PER_SET = 12  # maximum candidates in a set
MIN_MARKETS_PER_SET = 2   # minimum candidates in a set
FEE_RATE = 0.02           # per-trade fee rate

EXCLUDE_KEYWORDS = [
    "enter parliament",
    "win a seat",
    "qualify",
    "advance to",
    "make it",
    "reach the",
    "how many",
    "number of",
    "at least",
    "or more",
    "points or",
    "seats",
]

# ─── Helpers ─────────────────────────────────────────────────────────────────

def slug_to_name(slug: str) -> str:
    parts = slug.split("-")
    if parts[0] == "will":
        parts = parts[1:]
    return " ".join(word.capitalize() for word in parts[:2])


def calculate_threshold(n: int) -> float:
    return round(1.0 - (FEE_RATE * n), 2)


# ─── Step 1: Fetch All Events (sync) ─────────────────────────────────────────

def fetch_all_events() -> list:
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
            print(f"  Error fetching events at offset {offset}: {e}")
            break

        if not page:
            break

        for event in page:
            eid = event.get("id")
            if eid not in seen_ids:
                seen_ids.add(eid)
                all_events.append(event)

        if (offset + len(page)) % 500 < limit:
            print(f"  Fetched {offset + len(page)} events...")

        if len(page) < limit:
            break
        offset += limit

    return all_events


# ─── Step 2: Filter Exhaustive Sets (sync) ───────────────────────────────────

def filter_exhaustive_sets(events: list) -> list:
    valid = []

    for event in events:
        markets = event.get("markets", [])
        n = len(markets)

        if not (MIN_MARKETS_PER_SET <= n <= MAX_MARKETS_PER_SET):
            continue
        if event.get("closed", False):
            continue
        if not all(m.get("active", False) for m in markets):
            continue

        title = event.get("title", "").lower()
        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            continue

        valid.append(event)

    return valid


# ─── Step 3: Fetch All Prices Concurrently (async) ───────────────────────────

async def fetch_midpoint(session: aiohttp.ClientSession, token_id: str):
    url = "https://clob.polymarket.com/midpoint"
    params = {"token_id": token_id}
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return float(data.get("mid", 0))
    except Exception:
        return None


async def fetch_token_ids(session: aiohttp.ClientSession, slug: str) -> dict:
    url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return {}
            data = await resp.json()
            outcomes = json.loads(data["outcomes"])
            token_ids = json.loads(data["clobTokenIds"])
            return dict(zip(outcomes, token_ids))
    except Exception:
        return {}


async def fetch_prices_for_event(session: aiohttp.ClientSession, event: dict):
    prices = {}
    for market in event["markets"]:
        slug = market.get("slug")
        if not slug:
            return None

        token_map = await fetch_token_ids(session, slug)
        if not token_map:
            return None

        yes_token = token_map.get("Yes")
        if not yes_token:
            return None

        price = await fetch_midpoint(session, yes_token)
        if price is None:
            return None

        name = slug_to_name(slug)
        prices[name] = price

    return prices


async def fetch_all_prices(valid_events: list) -> list:
    connector = aiohttp.TCPConnector(limit=50)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            fetch_prices_for_event(session, event)
            for event in valid_events
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return results


def fetch_all_prices_concurrent(valid_events: list) -> list:
    return asyncio.run(fetch_all_prices(valid_events))


# ─── Step 4: Score and Filter (sync) ─────────────────────────────────────────

def score_and_filter(valid_events: list, price_results: list) -> list:
    scored = []

    for event, prices in zip(valid_events, price_results):
        if not prices or isinstance(prices, Exception):
            continue
        if len(prices) < MIN_MARKETS_PER_SET:
            continue

        n = len(prices)
        cycle_sum = round(sum(prices.values()), 4)
        threshold = calculate_threshold(n)
        edge = round(threshold - cycle_sum, 4)

        # Skip sets with implausibly low sums (bad data, not real arb)
        if cycle_sum < 0.3:
            continue

        scored.append({
            "name": event.get("title", event.get("slug")),
            "slug": event.get("slug", ""),
            "n": n,
            "cycle_sum": cycle_sum,
            "threshold": threshold,
            "edge": edge,
            "opportunity": edge >= 0,
            "prices": prices,
        })

    scored.sort(key=lambda x: x["edge"], reverse=True)
    return scored


# ─── Step 5: Run Loop ─────────────────────────────────────────────────────────

def run():
    print("PolymarketBot Live Scanner")
    print(f"Bankroll: $10,000 | Scan interval: 2 min | Fee rate: 2% per outcome")
    print(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    sheet = get_sheet()
    if sheet is None:
        print("Google Sheets connection failed — exiting")
        return

    last_logged = {}      # slug → timestamp of last log
    scan_count = 0
    total_opportunities = 0

    try:
        while True:
            scan_count += 1
            scan_start = time.time()
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] Scan #{scan_count} starting...")

            # Fetch and filter events
            events = fetch_all_events()
            valid = filter_exhaustive_sets(events)
            print(f"  {len(events)} events → {len(valid)} valid exhaustive sets")

            # Fetch all prices concurrently
            print(f"  Fetching prices concurrently...")
            price_results = fetch_all_prices_concurrent(valid)

            # Score and filter
            scored = score_and_filter(valid, price_results)
            scan_duration = round(time.time() - scan_start, 1)

            # Separate opportunities from near-misses
            opportunities = [s for s in scored if s["opportunity"]]
            near_misses = [s for s in scored if not s["opportunity"]][:5]

            # Print and log opportunities
            if opportunities:
                print(f"\n  ── ARB OPPORTUNITIES ({len(opportunities)}) ──")
                for s in opportunities:
                    print(f"  ★ {s['name']}")
                    print(f"    sum={s['cycle_sum']}  threshold={s['threshold']}  edge=+{s['edge']}")
                    for name, price in s["prices"].items():
                        print(f"    {name}: {price}")

                    now = time.time()
                    on_cooldown = (
                        s["slug"] in last_logged
                        and now - last_logged[s["slug"]] < COOLDOWN
                    )

                    if not on_cooldown:
                        stakes = calculate_stakes(s["prices"], s["cycle_sum"], BANKROLL)
                        log_opportunity(sheet, s["name"], stakes, s["prices"])
                        last_logged[s["slug"]] = now
                        total_opportunities += 1
                        print(f"    → Logged to Sheets ✓")
                    else:
                        remaining = round((COOLDOWN - (now - last_logged[s["slug"]])) / 60)
                        print(f"    → Cooldown active ({remaining} min remaining)")
            else:
                print(f"  No opportunities found.")

            # Print top 5 near misses
            print(f"\n  ── CLOSEST TO ARB ──")
            for s in near_misses:
                print(f"  {s['name']:<45} sum={s['cycle_sum']}  edge={s['edge']}")

            # Scan summary
            print(f"\n  Scan #{scan_count} complete in {scan_duration}s")
            print(f"  Sets scored: {len(scored)} | Total opportunities logged: {total_opportunities}")

            # Sleep until next scan
            sleep_time = max(0, SCAN_INTERVAL - (time.time() - scan_start))
            print(f"  Next scan in {round(sleep_time)}s...")
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        print(f"\n── SESSION SUMMARY ──────────────────────")
        print(f"  Total scans: {scan_count}")
        print(f"  Total opportunities logged: {total_opportunities}")
        print(f"  Duration: ~{round(scan_count * SCAN_INTERVAL / 60)} minutes")
        print(f"Bot stopped.")


# ─── Entry Point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run()
