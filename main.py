"""Orchestrates the 5-second polling loop, wiring all six modules from market discovery through opportunity detection to position logging."""

import time
import datetime
import os

from polymarket_client import get_prices_for_market
from market_curator import EXHAUSTIVE_SETS, SYNTHETIC_PRICES, TEST_MODE
from graph import detect_arbitrage
from position_sizer import calculate_stakes
from sheets_logger import get_sheet, log_opportunity

BANKROLL = 10000.0
POLL_INTERVAL = 5    # seconds between scans
COOLDOWN = 60        # seconds before re-logging same set


def run():
    # ── STEP 1: Startup ──────────────────────────────────────────────────────
    mode_label = "TEST" if TEST_MODE else "LIVE"
    print("PolymarketBot starting...")
    print(f"Mode: {mode_label} | Sets loaded: {len(EXHAUSTIVE_SETS)} | Bankroll: ${BANKROLL:,.0f}")

    sheet = get_sheet()
    if sheet is None:
        print("Error: could not connect to Google Sheets. Exiting.")
        return

    last_logged = {}

    # ── STEP 2: Polling loop ─────────────────────────────────────────────────
    try:
        while True:
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"\n[{timestamp}] Scanning {len(EXHAUSTIVE_SETS)} markets...")

            for set_name, set_data in EXHAUSTIVE_SETS.items():

                # STEP 2a — Get prices
                if TEST_MODE:
                    if set_name not in SYNTHETIC_PRICES:
                        print(f"  WARNING: '{set_name}' missing from SYNTHETIC_PRICES — skipping.")
                        continue
                    prices = SYNTHETIC_PRICES[set_name]
                else:
                    prices = {}
                    skip_set = False
                    for slug in set_data["slugs"]:
                        result = get_prices_for_market(slug)
                        if not result:
                            print(f"  WARNING: no prices returned for slug '{slug}' — skipping set.")
                            skip_set = True
                            break
                        prices.update(result)
                    if skip_set:
                        continue

                # STEP 2b — Detect arbitrage
                threshold = set_data["threshold"]
                arb_result = detect_arbitrage(prices, threshold)
                cycle_sum = arb_result["cycle_sum"]

                # STEP 2c — Check cooldown
                now = time.time()
                on_cooldown = (set_name in last_logged and
                               now - last_logged[set_name] < COOLDOWN)

                # STEP 2d — Print and log
                if arb_result["opportunity"]:
                    stakes_result = calculate_stakes(prices, cycle_sum, BANKROLL)
                    profit = stakes_result["expected_profit"]
                    print(f"  {set_data['name']:<35} sum={cycle_sum:.4f}  ← ARB DETECTED  profit=${profit:.2f}")
                    if not on_cooldown:
                        log_opportunity(sheet, set_data["name"], stakes_result, prices)
                        last_logged[set_name] = now
                    else:
                        print("  (cooldown active — not logging)")
                else:
                    print(f"  {set_data['name']:<35} sum={cycle_sum:.4f}  no opportunity")

            print()
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("Bot stopped.")


if __name__ == "__main__":
    run()
