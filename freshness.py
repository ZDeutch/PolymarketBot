"""
Freshness gate — blocks main.py / backtest.runner from executing simulations
unless every input the bot relies on has been confirmed fresh.

Two kinds of inputs:

  1. Auto-detected freshness (file mtime or in-file timestamp):
       - tpr_data.json           — uses 'generated_at'
       - hardcoded_rounds        — uses HARDCODED_ROUNDS coverage of the
                                   tournament being run

  2. User-attested freshness (recorded in `.freshness.json`):
       - kalshi_prices           — hand-copied from kalshi.com market page
       - polymarket_slugs        — hand-mapped to current event
       - tour_id_<tournament>    — Lichess tour ID for an active broadcast

Usage:
    python -m freshness status
    python -m freshness mark kalshi_prices --tournament "GCT Poland 2026"
    python -m freshness mark tpr_data        # equivalent to rebuilding TPR
    python -m freshness mark tour_id "Norway Chess 2026" --value BLA70Vds

The gate is invoked from main.py's run() and refuses to trade if any
required input is stale. Override with --skip-freshness-check (research only).
"""

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

LEDGER_PATH = os.path.join(os.path.dirname(__file__), ".freshness.json")
TPR_PATH    = os.path.join(os.path.dirname(__file__), "tpr_data.json")

# ─── Max-age policy per input ────────────────────────────────────────────────

# Keys are policy IDs; values are max age (hours) before stale.
MAX_AGE_HOURS = {
    "tpr_data":         24,    # rebuild daily
    "kalshi_prices":    2,     # market moves fast
    "polymarket_slugs": 168,   # one week — slugs rarely change mid-event
    "tour_id":          720,   # tour IDs are stable for the event lifetime
}


# ─── Ledger I/O ──────────────────────────────────────────────────────────────

def _load_ledger() -> dict:
    if not os.path.exists(LEDGER_PATH):
        return {}
    try:
        with open(LEDGER_PATH) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_ledger(ledger: dict) -> None:
    with open(LEDGER_PATH, "w") as f:
        json.dump(ledger, f, indent=2, sort_keys=True)


def mark_fresh(key: str, value: Any = None,
               tournament: str | None = None) -> None:
    """Record a freshness attestation in `.freshness.json`."""
    ledger = _load_ledger()
    entry = {"refreshed_at": datetime.now().isoformat()}
    if value is not None:
        entry["value"] = value
    if tournament is not None:
        entry["tournament"] = tournament
    ledger[key] = entry
    _save_ledger(ledger)


# ─── Status checks ───────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    key:       str
    fresh:     bool
    age_hours: float | None
    detail:    str
    action:    str   # what user must do to fix


def _check_age(refreshed_at: str | None, max_hours: int) -> tuple[bool, float | None]:
    if not refreshed_at:
        return False, None
    try:
        ts = datetime.fromisoformat(refreshed_at)
    except ValueError:
        return False, None
    age = (datetime.now() - ts).total_seconds() / 3600.0
    return age <= max_hours, age


def check_tpr_data() -> CheckResult:
    if not os.path.exists(TPR_PATH):
        return CheckResult(
            "tpr_data", False, None,
            "tpr_data.json missing",
            "Run: venv/bin/python tpr_builder.py",
        )
    try:
        with open(TPR_PATH) as f:
            generated_at = json.load(f).get("generated_at")
    except Exception as e:
        return CheckResult(
            "tpr_data", False, None,
            f"could not read tpr_data.json: {e}",
            "Run: venv/bin/python tpr_builder.py",
        )
    fresh, age = _check_age(generated_at, MAX_AGE_HOURS["tpr_data"])
    return CheckResult(
        "tpr_data", fresh, age,
        f"generated_at={generated_at}",
        "Run: venv/bin/python tpr_builder.py",
    )


def check_kalshi_prices(tournament: str) -> CheckResult:
    """Two valid paths satisfy this check:

      A. (preferred) `kalshi_event::<tournament>` is recorded in the ledger
         → main.py auto-fetches live prices via the Kalshi API at run time.
      B. Manual KALSHI_PRICES update in main.py + ledger attestation.
    """
    ledger = _load_ledger()
    event_key = f"kalshi_event::{tournament}"
    if ledger.get(event_key, {}).get("value"):
        return CheckResult(
            "kalshi_prices", True, 0.0,
            f"auto-fetch via {ledger[event_key]['value']}",
            "(no action — live API fetch)",
        )
    entry = ledger.get("kalshi_prices", {})
    fresh, age = _check_age(entry.get("refreshed_at"),
                            MAX_AGE_HOURS["kalshi_prices"])
    if entry.get("tournament") and entry["tournament"] != tournament:
        fresh = False
    return CheckResult(
        "kalshi_prices", fresh, age,
        f"tournament={entry.get('tournament', 'unset')} (no event_ticker mapped)",
        f"PREFERRED: record the Kalshi event ticker (auto-fetches every run):\n"
        f"     venv/bin/python -m freshness mark kalshi_event "
        f"\"{tournament}\" --value <EVENT_TICKER>\n"
        f"   FALLBACK: hand-update KALSHI_PRICES in main.py, then:\n"
        f"     venv/bin/python -m freshness mark kalshi_prices "
        f"--tournament \"{tournament}\"",
    )


def get_kalshi_event(tournament: str) -> str | None:
    """Returns the Kalshi event ticker for a tournament if recorded."""
    ledger = _load_ledger()
    return ledger.get(f"kalshi_event::{tournament}", {}).get("value")


def check_polymarket_slugs(tournament: str) -> CheckResult:
    ledger = _load_ledger()
    entry  = ledger.get("polymarket_slugs", {})
    fresh, age = _check_age(entry.get("refreshed_at"),
                            MAX_AGE_HOURS["polymarket_slugs"])
    if entry.get("tournament") and entry["tournament"] != tournament:
        fresh = False
    return CheckResult(
        "polymarket_slugs", fresh, age,
        f"tournament={entry.get('tournament', 'unset')}",
        f"1) Set POLYMARKET_SLUGS in main.py for '{tournament}'\n"
        f"   2) Run: venv/bin/python -m freshness mark polymarket_slugs "
        f"--tournament \"{tournament}\"",
    )


def check_tour_id(tournament: str) -> CheckResult:
    """Tour IDs are stable; we just need one to exist for active tournaments."""
    ledger = _load_ledger()
    key    = f"tour_id::{tournament}"
    entry  = ledger.get(key, {})
    if not entry.get("value"):
        return CheckResult(
            key, False, None,
            "no tour ID recorded",
            f"Find the 8-char tour ID at lichess.org/broadcast and run:\n"
            f"   venv/bin/python -m freshness mark tour_id "
            f"\"{tournament}\" --value <ID>",
        )
    fresh, age = _check_age(entry.get("refreshed_at"),
                            MAX_AGE_HOURS["tour_id"])
    return CheckResult(
        key, fresh, age,
        f"tour_id={entry['value']}",
        f"Re-confirm tour ID and run: "
        f"venv/bin/python -m freshness mark tour_id "
        f"\"{tournament}\" --value <ID>",
    )


def get_tour_id(tournament: str) -> str | None:
    """Returns the recorded tour ID for a tournament, or None."""
    ledger = _load_ledger()
    return ledger.get(f"tour_id::{tournament}", {}).get("value")


# ─── Composite gate ──────────────────────────────────────────────────────────

def required_checks(tournament: str,
                    exchange: str,
                    no_broadcast: bool) -> list[CheckResult]:
    """Returns the list of checks that must all pass for this run."""
    checks: list[CheckResult] = [check_tpr_data()]
    if exchange == "kalshi":
        checks.append(check_kalshi_prices(tournament))
    elif exchange == "polymarket":
        checks.append(check_polymarket_slugs(tournament))
    if not no_broadcast:
        checks.append(check_tour_id(tournament))
    return checks


def assert_fresh(tournament: str, exchange: str, no_broadcast: bool) -> None:
    """Hard gate. Prints a report and sys.exits if anything is stale."""
    results = required_checks(tournament, exchange, no_broadcast)
    bad = [r for r in results if not r.fresh]
    if not bad:
        print("\n[freshness] all inputs fresh:")
        for r in results:
            age = f"{r.age_hours:.1f}h" if r.age_hours is not None else " n/a"
            print(f"   ✓ {r.key:<32} age {age}  {r.detail}")
        return

    print("\n[freshness] BLOCKED — stale or missing inputs:")
    for r in results:
        age = f"{r.age_hours:.1f}h" if r.age_hours is not None else " n/a"
        mark = "✗" if not r.fresh else "✓"
        print(f"   {mark} {r.key:<32} age {age}  {r.detail}")
    print("\nFix the items above, then re-run. Required actions:")
    for r in bad:
        print(f"\n[{r.key}]")
        print(f"   {r.action}")
    print("\nTo bypass for research only, pass --skip-freshness-check.")
    sys.exit(2)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _cmd_status() -> None:
    """Print all known checks (no tournament-specific gating)."""
    print(f"Ledger: {LEDGER_PATH}")
    ledger = _load_ledger()
    if not ledger:
        print("(empty — no inputs marked fresh yet)")
    print()
    print(check_tpr_data())
    for k, v in ledger.items():
        ts   = v.get("refreshed_at", "?")
        meta = {x: v[x] for x in v if x != "refreshed_at"}
        print(f"  {k:<40} {ts}  {meta}")


def _cmd_mark(args) -> None:
    if args.key == "tpr_data":
        # Rebuilding the file is the actual mark; just print a hint.
        print("Run: venv/bin/python tpr_builder.py")
        print("(tpr_data freshness is auto-derived from the file's generated_at.)")
        return
    if args.key in ("tour_id", "kalshi_event"):
        if not args.tournament or not args.value:
            print(f"ERROR: tournament and --value required for {args.key} mark")
            sys.exit(1)
        mark_fresh(f"{args.key}::{args.tournament}",
                   value=args.value, tournament=args.tournament)
        print(f"Marked {args.key}::{args.tournament} = {args.value}")
        return
    mark_fresh(args.key, tournament=args.tournament, value=args.value)
    print(f"Marked {args.key} fresh"
          f"{' for ' + args.tournament if args.tournament else ''}.")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Freshness ledger CLI.")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("status", help="Show ledger + computed staleness")
    sp.set_defaults(func=lambda a: _cmd_status())

    sp = sub.add_parser("mark",
                        help="Mark an input as fresh in the ledger")
    sp.add_argument("key", help="Ledger key (e.g. kalshi_prices, tour_id)")
    sp.add_argument("tournament", nargs="?", default=None)
    sp.add_argument("--tournament", dest="tournament_flag", default=None,
                    help="Tournament name (alternative to positional)")
    sp.add_argument("--value", default=None,
                    help="Optional value (e.g. tour ID)")
    sp.set_defaults(func=lambda a: _cmd_mark(a))

    args = p.parse_args()
    if hasattr(args, "tournament_flag") and args.tournament_flag:
        args.tournament = args.tournament_flag
    args.func(args)
