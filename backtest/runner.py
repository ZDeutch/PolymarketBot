"""
Backtest runner — replays each historical case through the simulator
and reports metrics.

Usage:
    python -m backtest.runner
    python -m backtest.runner --n 20000 --case "Norway Chess 2024"
    python -m backtest.runner --oos                  # out-of-sample asof rebuild
    python -m backtest.runner --oos --no-recency     # A/B baseline

By default the runner uses the current tpr_data.json (in-sample, fast).
Pass --oos to rebuild the TPR dataset per case using each case's
start_date as the asof cutoff. This gives a true out-of-sample read but
takes much longer (one PGN scrape per case).
"""

import argparse
import os
import sys

# Make project root importable when run as `python -m backtest.runner`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from itertools import permutations

from backtest.cases import CASES, BacktestCase
from backtest.metrics import (
    log_loss, brier_score, market_log_loss,
    calibration_buckets, quarter_kelly_pnl,
)
from simulators import chess_simulator
import tpr_builder


def build_remaining_schedule(players: list, total_rounds: int) -> list[tuple]:
    """All ordered pairs for a single or double round-robin.

    total_rounds == n − 1   → single round-robin
    total_rounds == 2(n − 1) → double round-robin
    Otherwise treated as single round-robin.
    """
    n = len(players)
    if total_rounds >= 2 * (n - 1):
        # Double round-robin: each ordered pair plays once
        return list(permutations(players, 2))
    # Single round-robin: each unordered pair plays once;
    # use canonical (p_lex_smaller as white) for determinism.
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            pairs.append((players[i], players[j]))
    return pairs


def _ensure_oos_tpr(case: BacktestCase, recency: bool, cache_dir: str) -> str:
    """Builds (or reuses) a per-case asof-truncated TPR dataset.

    Returns the path to the JSON dataset to use for this case.
    """
    if not case.start_date:
        raise ValueError(f"Case {case.name!r} has no start_date — cannot run --oos")
    os.makedirs(cache_dir, exist_ok=True)
    tag = case.name.lower().replace(" ", "_")
    suffix = "" if recency else "_norec"
    path = os.path.join(cache_dir, f"tpr_{tag}_{case.start_date}{suffix}.json")
    if os.path.exists(path):
        print(f"  [cached] {path}")
        return path
    print(f"  [building] {path} (asof={case.start_date}, recency={recency})")
    tpr_builder.run(asof=case.start_date, recency=recency, output_path=path)
    return path


def run_case(case: BacktestCase, n_iters: int = 20000,
             oos: bool = False, recency: bool = True,
             cache_dir: str = "backtest/_tpr_cache") -> dict:
    """Simulates one historical case pre-tournament; returns metrics.

    If oos=True, rebuilds the TPR dataset using case.start_date as the
    asof cutoff. recency toggles the recency-weighting A/B knob.
    """
    if oos:
        path = _ensure_oos_tpr(case, recency=recency, cache_dir=cache_dir)
        chess_simulator.set_tpr_data_path(path)
    else:
        chess_simulator.set_tpr_data_path(None)

    remaining = build_remaining_schedule(case.players, case.total_rounds)

    # Empty completed schedule — pre-tournament replay
    completed: dict = {}

    print(f"\n— {case.name} —")
    print(f"  {len(case.players)} players, {len(remaining)} games, "
          f"{case.total_rounds} rounds")
    print(f"  Actual winner(s): {', '.join(case.actual_winners)}")

    probs = chess_simulator.simulate_tournament(
        ratings=case.fide_ratings,
        completed=completed,
        remaining=remaining,
        n=n_iters,
    )

    # Sort and print top 5
    top5 = sorted(probs.items(), key=lambda kv: -kv[1])[:5]
    for name, p in top5:
        marker = " *" if name in case.actual_winners else ""
        print(f"    {name:<28}  {p*100:>5.1f}%{marker}")

    ll  = log_loss(probs, case.actual_winners)
    br  = brier_score(probs, case.actual_winners)
    mll = market_log_loss(case.market_prices or {}, case.actual_winners)
    pnl = quarter_kelly_pnl(probs, case.market_prices or {},
                            case.actual_winners)

    print(f"  log-loss(model):  {ll:.3f}")
    if mll is not None:
        print(f"  log-loss(market): {mll:.3f}  (Δ {ll - mll:+.3f})")
    print(f"  brier:            {br:.3f}")
    if pnl["trades"]:
        print(f"  trades: {pnl['trades']}  stake ${pnl['stake']:.0f}  "
              f"realized P&L ${pnl['pnl']:+.0f}")

    return {
        "case":         case.name,
        "model_probs":  probs,
        "log_loss":     ll,
        "brier":        br,
        "market_ll":    mll,
        "pnl":          pnl,
        "winners":      case.actual_winners,
    }


def aggregate(results: list[dict]) -> None:
    """Prints aggregate metrics across all cases."""
    print("\n" + "=" * 60)
    print("AGGREGATE")
    print("=" * 60)

    n = len(results)
    if not n:
        print("No cases run.")
        return

    avg_ll = sum(r["log_loss"] for r in results) / n
    avg_br = sum(r["brier"]    for r in results) / n
    print(f"  cases:            {n}")
    print(f"  avg log-loss:     {avg_ll:.3f}")
    print(f"  avg brier:        {avg_br:.3f}")

    market_results = [r for r in results if r["market_ll"] is not None]
    if market_results:
        avg_mll = sum(r["market_ll"] for r in market_results) / len(market_results)
        print(f"  avg log-loss(market): {avg_mll:.3f}  "
              f"(Δ {avg_ll - avg_mll:+.3f})")
        total_pnl = sum(r["pnl"]["pnl"] for r in market_results)
        total_stk = sum(r["pnl"]["stake"] for r in market_results)
        total_trd = sum(r["pnl"]["trades"] for r in market_results)
        print(f"  total trades:     {total_trd}")
        print(f"  total stake:      ${total_stk:,.0f}")
        print(f"  total P&L:        ${total_pnl:+,.0f}  "
              f"({total_pnl/total_stk*100:+.1f}% ROI)" if total_stk else "")

    # Calibration across all (model_p, won) pairs
    preds = []
    for r in results:
        winners = set(r["winners"])
        for player, p in r["model_probs"].items():
            preds.append((p, player in winners))
    buckets = calibration_buckets(preds, n_buckets=10)
    print("\n  Calibration (model probability vs realized hit rate):")
    print(f"    {'bucket':>10}  {'n':>4}  {'pred':>6}  {'hit':>6}")
    for b in buckets:
        if b["n"] == 0:
            continue
        print(f"    {b['low']:.1f}–{b['high']:.1f}    {b['n']:>4}  "
              f"{b['mean_pred']*100:>5.1f}%  {b['hit_rate']*100:>5.1f}%")


def main() -> None:
    ap = argparse.ArgumentParser(description="Run backtest on historical cases.")
    ap.add_argument("--n", type=int, default=20000,
                    help="Monte Carlo iterations per case (default 20000)")
    ap.add_argument("--case", type=str, default=None,
                    help="Run only the case with this exact name")
    ap.add_argument("--oos", action="store_true",
                    help="Out-of-sample: rebuild TPR per case using case.start_date as asof.")
    ap.add_argument("--no-recency", dest="recency", action="store_false",
                    help="Disable recency weighting (A/B baseline). Implies --oos.")
    ap.add_argument("--ab", action="store_true",
                    help="Run both with-recency and no-recency variants and print delta.")
    ap.set_defaults(recency=True)
    args = ap.parse_args()

    cases = CASES
    if args.case:
        cases = [c for c in CASES if c.name == args.case]
        if not cases:
            print(f"No case found named: {args.case}")
            print(f"Available: {[c.name for c in CASES]}")
            sys.exit(1)

    # --no-recency only meaningful when rebuilding TPR (otherwise the existing
    # tpr_data.json already has recency baked in). Force --oos in that case.
    oos = args.oos or args.ab or not args.recency

    if args.ab:
        print("\n" + "=" * 60)
        print("A/B HARNESS: recency=ON")
        print("=" * 60)
        on  = [run_case(c, n_iters=args.n, oos=True, recency=True) for c in cases]
        aggregate(on)
        print("\n" + "=" * 60)
        print("A/B HARNESS: recency=OFF")
        print("=" * 60)
        off = [run_case(c, n_iters=args.n, oos=True, recency=False) for c in cases]
        aggregate(off)
        print("\n" + "=" * 60)
        print("A/B SUMMARY (recency on − off; negative = recency wins)")
        print("=" * 60)
        for r_on, r_off in zip(on, off):
            d_ll = r_on["log_loss"] - r_off["log_loss"]
            d_br = r_on["brier"]    - r_off["brier"]
            print(f"  {r_on['case']:<25}  Δlog-loss {d_ll:+.3f}  Δbrier {d_br:+.3f}")
        avg_d_ll = sum(a["log_loss"] - b["log_loss"] for a, b in zip(on, off)) / len(on)
        avg_d_br = sum(a["brier"]    - b["brier"]    for a, b in zip(on, off)) / len(on)
        print(f"  {'AVG':<25}  Δlog-loss {avg_d_ll:+.3f}  Δbrier {avg_d_br:+.3f}")
        return

    results = [run_case(c, n_iters=args.n, oos=oos, recency=args.recency)
               for c in cases]
    aggregate(results)


if __name__ == "__main__":
    main()
