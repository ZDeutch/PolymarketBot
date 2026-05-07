#!/usr/bin/env python3
"""
P&L dashboard server for PolymarketBot.

Serves dashboard.html at / and live portfolio data at /api/pnl.
Fetches Kalshi prices on every request; results are fresh within the
browser's 30-second auto-refresh cadence.

Usage:
    venv/bin/python server.py [--port 8080]
Then open: http://localhost:8080/
"""
import csv
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from fetchers.kalshi_fetcher import KalshiClient
from freshness import _load_ledger

PORT         = 8080
CSV_FILE     = "positions.csv"
REFRESH_SECS = 30   # forwarded to dashboard for auto-poll cadence


# ── Data helpers ─────────────────────────────────────────────────────────────

def _event_ticker(tournament: str) -> str | None:
    return _load_ledger().get(f"kalshi_event::{tournament}", {}).get("value")


def _load_positions() -> list[dict]:
    if not os.path.exists(CSV_FILE):
        return []
    with open(CSV_FILE, newline="") as f:
        reader = csv.DictReader(f)
        return [r for r in reader if (r.get("Player") or "").strip()]


def compute_pnl() -> dict:
    positions = _load_positions()
    client    = KalshiClient()

    # One API call per unique event ticker
    prices: dict[str, dict] = {}
    for pos in positions:
        ticker = _event_ticker(pos["Tournament"])
        if ticker and ticker not in prices:
            try:
                prices[ticker] = client.get_event_prices(ticker)
            except Exception:
                prices[ticker] = {}

    rows: list[dict] = []
    total_invested = 0.0
    total_value    = 0.0

    for pos in positions:
        player      = pos["Player"].strip()
        action      = (pos.get("Action") or "").strip().upper()
        stake       = float(pos.get("Stake")      or 0)
        entry_price = float(pos.get("EntryPrice") or 0)
        ticker      = _event_ticker(pos["Tournament"])

        contracts = round(stake / entry_price, 4) if entry_price else 0
        mkt       = prices.get(ticker or "", {}).get(player, {})
        yes_mid   = mkt.get("mid")

        if yes_mid is not None:
            cur_price = yes_mid if action == "YES" else (1 - yes_mid)
            cur_value = round(contracts * cur_price, 2)
            pnl       = round(cur_value - stake, 2)
            pnl_pct   = round(pnl / stake * 100, 2) if stake else 0
            data_ok   = True
        else:
            cur_price = cur_value = pnl = pnl_pct = None
            data_ok = False

        total_invested += stake
        if cur_value is not None:
            total_value += cur_value

        rows.append({
            "date":               (pos.get("Date") or "").strip(),
            "tournament":         pos["Tournament"].strip(),
            "event_ticker":       ticker,
            "exchange":           (pos.get("Exchange") or "").strip(),
            "player":             player,
            "model_pct":          float(pos.get("Model%")  or 0),
            "market_pct":         float(pos.get("Market%") or 0),
            "edge_pct":           float(pos.get("Edge%")   or 0),
            "action":             action,
            "stake":              stake,
            "entry_price":        entry_price,
            "contracts":          contracts,
            "yes_mid":            yes_mid,
            "current_price":      cur_price,
            "current_value":      cur_value,
            "unrealized_pnl":     pnl,
            "unrealized_pnl_pct": pnl_pct,
            "data_ok":            data_ok,
        })

    total_pnl     = round(total_value - total_invested, 2)
    total_pnl_pct = round(total_pnl / total_invested * 100, 2) if total_invested else 0

    return {
        "updated_at":     datetime.now().isoformat(),
        "total_invested": round(total_invested, 2),
        "total_value":    round(total_value,    2),
        "total_pnl":      total_pnl,
        "total_pnl_pct":  total_pnl_pct,
        "refresh_secs":   REFRESH_SECS,
        "positions":      rows,
    }


# ── HTTP handler ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass   # suppress access log

    def _json(self, data: dict, status: int = 200) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", len(body))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: str, ctype: str) -> None:
        try:
            with open(path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type",   ctype)
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self.send_error(404)

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/api/pnl":
            try:
                self._json(compute_pnl())
            except Exception as e:
                self._json({"error": str(e)}, 500)

        elif path in ("/", "/dashboard"):
            self._file("dashboard.html", "text/html; charset=utf-8")

        elif path == "/methodology":
            self._file("methodology.html", "text/html; charset=utf-8")

        elif path == "/positions.csv":
            self._file("positions.csv", "text/plain")

        else:
            self.send_error(404)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="PolymarketBot P&L dashboard server.")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()

    server = ThreadingHTTPServer(("", args.port), Handler)
    print(f"[pnl-server] → http://localhost:{args.port}/")
    print(f"[pnl-server]   /api/pnl  — live portfolio JSON")
    print("[pnl-server] Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
