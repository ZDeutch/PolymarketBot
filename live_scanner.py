"""Continuously scans ALL open Polymarket events for exhaustive sets and logs
genuine arb opportunities to Google Sheets. Replaces hardcoded market_curator.py.
Prices are read from outcomePrices embedded in the Gamma API event response —
no CLOB calls needed."""

import requests
import json
import time
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
MIN_CYCLE_SUM = 0.5       # skip sets with implausibly low sums (stale/non-exclusive)
MAX_CYCLE_SUM = 1.15      # skip sets where sum > 1.15 (structurally non-exclusive)

REQUIRED_KEYWORDS = [
    "winner", "win", "president", "senator",
    "governor", "primary", "election", "nominee",
    "championship", "champion", "tournament",
]

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
    "margin of victory",
    "margin",
    "winners",
    "primary winners",
    "which candidates",
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
    # Stage 1: market count (2–MAX_MARKETS_PER_SET)
    after_count = [
        e for e in events
        if MIN_MARKETS_PER_SET <= len(e.get("markets", [])) <= MAX_MARKETS_PER_SET
    ]
    print(f"  After market count filter:   {len(after_count)}")

    # Stage 2: not closed
    after_closed = [e for e in after_count if not e.get("closed", False)]
    print(f"  After closed filter:         {len(after_closed)}")

    # Stage 3: all markets active
    after_active = [
        e for e in after_closed
        if all(m.get("active", False) for m in e.get("markets", []))
    ]
    print(f"  After active filter:         {len(after_active)}")

    # Stage 4: title must contain a required keyword
    after_required = [
        e for e in after_active
        if any(kw in e.get("title", "").lower() for kw in REQUIRED_KEYWORDS)
    ]
    print(f"  After required keywords:     {len(after_required)}")

    # Stage 5: title must not contain an excluded keyword
    after_exclude = [
        e for e in after_required
        if not any(kw in e.get("title", "").lower() for kw in EXCLUDE_KEYWORDS)
    ]
    print(f"  After exclude keywords:      {len(after_exclude)}")

    # Stage 6: at least some trading volume across all markets
    after_volume = [
        e for e in after_exclude
        if sum(float(m.get("volumeNum", 0)) for m in e.get("markets", [])) > 0
    ]
    print(f"  After zero volume filter:    {len(after_volume)}")
    print(f"  Final valid sets:            {len(after_volume)}")

    return after_volume


# ─── Step 3: Extract Prices from Gamma Event Data (sync) ─────────────────────

def extract_prices_from_event(event: dict) -> dict:
    """Reads Yes prices from outcomePrices already embedded in the Gamma event
    response. No additional API calls needed — all data is in hand from
    fetch_all_events(). Returns {} if any market is missing prices, has no
    Yes outcome, or produces a slug_to_name collision."""
    prices = {}

    for market in event["markets"]:
        slug = market.get("slug", "")
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

        # Skip prices at the extremes — market not yet active or already resolved
        if yes_price <= 0.01 or yes_price >= 0.99:
            return {}

        name = slug_to_name(slug)
        if name in prices:
            # Two markets in this event share the same short name —
            # not a true exhaustive set (e.g. deadline variants).
            return {}
        prices[name] = yes_price

    if len(prices) < MIN_MARKETS_PER_SET:
        return {}

    return prices


# ─── Step 4: Score and Filter (sync) ─────────────────────────────────────────

def score_and_filter(valid_events: list, price_results: list) -> list:
    # ── Debug breakdown ───────────────────────────────────────────────────────
    total = 0
    dropped_no_prices = 0
    dropped_min_sum = 0
    dropped_max_sum = 0
    kept = 0

    for event, prices in zip(valid_events, price_results):
        total += 1
        if not prices:
            dropped_no_prices += 1
            continue
        cycle_sum = round(sum(prices.values()), 4)
        if cycle_sum < MIN_CYCLE_SUM:
            dropped_min_sum += 1
            continue
        if cycle_sum > MAX_CYCLE_SUM:
            dropped_max_sum += 1
            continue
        kept += 1

    print(f"  Score breakdown:")
    print(f"    Total sets attempted:      {total}")
    print(f"    Dropped (no prices/empty): {dropped_no_prices}")
    print(f"    Dropped (sum < {MIN_CYCLE_SUM}):        {dropped_min_sum}")
    print(f"    Dropped (sum > {MAX_CYCLE_SUM}):       {dropped_max_sum}")
    print(f"    Kept for scoring:          {kept}")
    # ─────────────────────────────────────────────────────────────────────────

    scored = []

    for event, prices in zip(valid_events, price_results):
        if not prices:
            continue

        n = len(prices)
        cycle_sum = round(sum(prices.values()), 4)
        threshold = calculate_threshold(n)
        edge = round(threshold - cycle_sum, 4)

        if cycle_sum < MIN_CYCLE_SUM:
            continue
        if cycle_sum > MAX_CYCLE_SUM:
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

            # Extract inline Gamma prices (no CLOB calls) then score
            price_results = [extract_prices_from_event(event) for event in valid]
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
