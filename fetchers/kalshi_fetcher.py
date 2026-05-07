"""
Kalshi API client for fetching market data and historical prices.

Auth: RSA-PSS with SHA-256, signing concatenation of
      timestamp_ms + HTTP_method + path_without_query.

Required env vars (or pass explicitly to KalshiClient):
  KALSHI_ACCESS_KEY  — the Key ID
  KALSHI_PRIVATE_KEY — path to PEM private key file
  KALSHI_ENV         — "prod" (default) or "demo"

See: https://docs.kalshi.com/getting_started/api_keys

This is a minimal read-only client geared toward backtest needs:
  - get_event(event_ticker)
  - get_markets(event_ticker)
  - get_market(market_ticker)
  - get_market_history(market_ticker, period_interval=...) — closing prices
  - search_events(query)

The signing layer is dependency-light: only `requests` and `cryptography`
(already in the venv). Public endpoints work without keys.
"""

import base64
import datetime
import os
import time
from typing import Any
from urllib.parse import urlencode

import requests

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.backends import default_backend
    _CRYPTO_OK = True
except ImportError:
    _CRYPTO_OK = False


BASE_URLS = {
    "prod": "https://api.elections.kalshi.com",   # current prod host
    "demo": "https://demo-api.kalshi.co",
}


def _to_dollars(dollar_field: Any, cents_field: Any) -> float | None:
    """Kalshi sometimes returns dollar strings ('0.3100'), sometimes cents
    integers (31). Prefer the dollar-string field when present.
    """
    if dollar_field is not None:
        try:
            return round(float(dollar_field), 4)
        except (TypeError, ValueError):
            pass
    if cents_field is not None:
        try:
            return round(float(cents_field) / 100.0, 4)
        except (TypeError, ValueError):
            pass
    return None


class KalshiClient:
    """Minimal authenticated read client for Kalshi.

    Public endpoints (markets, events, market history) work without auth.
    Pass key_id + private_key_path to enable signed requests.
    """

    def __init__(self,
                 key_id: str | None = None,
                 private_key_path: str | None = None,
                 env: str = "prod",
                 timeout: float = 15.0):
        self.key_id            = key_id or os.environ.get("KALSHI_ACCESS_KEY")
        self.private_key_path  = private_key_path or os.environ.get("KALSHI_PRIVATE_KEY")
        self.env               = env or os.environ.get("KALSHI_ENV", "prod")
        self.base_url          = BASE_URLS.get(self.env, BASE_URLS["prod"])
        self.timeout           = timeout
        self._private_key      = None

    # ── Auth ────────────────────────────────────────────────────────────────

    def _load_key(self):
        if self._private_key is not None:
            return self._private_key
        if not self.private_key_path:
            return None
        if not _CRYPTO_OK:
            raise RuntimeError(
                "cryptography package required for signed requests. "
                "pip install cryptography"
            )
        with open(self.private_key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
        return self._private_key

    def _sign_headers(self, method: str, path_no_query: str) -> dict:
        """Returns Kalshi auth headers for the given request, or {} if no key."""
        key = self._load_key()
        if key is None or not self.key_id:
            return {}
        ts  = str(int(time.time() * 1000))
        msg = (ts + method.upper() + path_no_query).encode()
        sig = base64.b64encode(key.sign(
            msg,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )).decode()
        return {
            "KALSHI-ACCESS-KEY":       self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
        }

    # ── Core HTTP ───────────────────────────────────────────────────────────

    def _get(self, path: str, params: dict | None = None) -> Any:
        """GET request. `path` should start with /trade-api/v2/..."""
        url     = self.base_url + path
        headers = {"Accept": "application/json"}
        headers.update(self._sign_headers("GET", path))
        if params:
            url = url + "?" + urlencode(params)
        r = requests.get(url, headers=headers, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    # ── Public read endpoints ───────────────────────────────────────────────

    def search_events(self, query: str | None = None,
                      status: str = "open", limit: int = 100) -> list[dict]:
        """Lists events. status: 'open' | 'closed' | 'settled'."""
        params: dict = {"limit": limit, "status": status}
        if query:
            params["search"] = query
        data = self._get("/trade-api/v2/events", params=params)
        return data.get("events", [])

    def get_event(self, event_ticker: str) -> dict:
        """Returns event metadata + nested markets."""
        return self._get(f"/trade-api/v2/events/{event_ticker}")

    def get_markets(self, event_ticker: str | None = None,
                    status: str = "open", limit: int = 100) -> list[dict]:
        params: dict = {"limit": limit, "status": status}
        if event_ticker:
            params["event_ticker"] = event_ticker
        data = self._get("/trade-api/v2/markets", params=params)
        return data.get("markets", [])

    def get_market(self, market_ticker: str) -> dict:
        return self._get(f"/trade-api/v2/markets/{market_ticker}")

    def get_event_with_markets(self, event_ticker: str) -> dict:
        """Returns the event plus nested markets in one call.

        Used for player-name → market-ticker auto-mapping. Note that nested
        markets may have None bids/asks; call get_market(t) for live prices.
        """
        return self._get(f"/trade-api/v2/events/{event_ticker}",
                         params={"with_nested_markets": "true"})

    def find_event(self, search_terms: list[str],
                   series_ticker: str | None = None,
                   status: str = "open") -> dict | None:
        """Returns the first event whose title or sub_title contains any of
        the search terms (case-insensitive). If series_ticker is given,
        filters by that first.
        """
        params: dict = {"limit": 200, "status": status}
        if series_ticker:
            params["series_ticker"] = series_ticker
        events = self._get("/trade-api/v2/events", params=params).get("events", [])
        terms_lc = [t.lower() for t in search_terms]
        for ev in events:
            blob = (str(ev.get("title", "")) + " "
                    + str(ev.get("sub_title", "")) + " "
                    + str(ev.get("event_ticker", ""))).lower()
            if all(any(term in blob for term in terms_lc)
                   for term in [terms_lc[0]]):  # require first term, prefer all
                if all(term in blob for term in terms_lc):
                    return ev
        # Fallback: first event matching just the first term
        for ev in events:
            blob = (str(ev.get("title", "")) + " "
                    + str(ev.get("sub_title", "")) + " "
                    + str(ev.get("event_ticker", ""))).lower()
            if terms_lc[0] in blob:
                return ev
        return None

    # ── Live prices for an entire event ─────────────────────────────────────

    def get_event_prices(self, event_ticker: str) -> dict[str, dict]:
        """Returns {player_name: {ticker, yes_bid, yes_ask, last_price, mid}}.

        Uses the event's nested markets to get the player→ticker map, then
        hits each market individually to pick up live bid/ask. Prices in
        DOLLARS (not cents).
        """
        ev = self.get_event_with_markets(event_ticker)
        markets = ev.get("event", {}).get("markets", []) or ev.get("markets", [])
        out: dict[str, dict] = {}
        for nm in markets:
            ticker = nm.get("ticker")
            player = nm.get("yes_sub_title") or nm.get("subtitle") or ticker
            if not ticker:
                continue
            try:
                m = self.get_market(ticker).get("market", {})
            except Exception as e:
                print(f"  [kalshi] {ticker}: {e}")
                continue
            yes_bid = _to_dollars(m.get("yes_bid_dollars"), m.get("yes_bid"))
            yes_ask = _to_dollars(m.get("yes_ask_dollars"), m.get("yes_ask"))
            last    = _to_dollars(m.get("last_price_dollars"), m.get("last_price"))
            mid     = ((yes_bid + yes_ask) / 2.0
                       if yes_bid is not None and yes_ask is not None
                       else last)
            out[player] = {
                "ticker":  ticker,
                "yes_bid": yes_bid,
                "yes_ask": yes_ask,
                "last":    last,
                "mid":     mid,
            }
        return out

    def get_market_history(self, market_ticker: str,
                           start_ts: int | None = None,
                           end_ts: int | None = None,
                           period_interval: int = 60,
                           limit: int = 1000) -> list[dict]:
        """OHLC-style history. period_interval is minutes (1, 60, 1440 typical).

        Each candle: {"end_period_ts", "open_interest", "yes_bid_*", "yes_ask_*",
                       "yes_price_*", "volume", ...}
        """
        params: dict = {"limit": limit, "period_interval": period_interval}
        if start_ts is not None:
            params["start_ts"] = start_ts
        if end_ts is not None:
            params["end_ts"] = end_ts
        data = self._get(
            f"/trade-api/v2/markets/{market_ticker}/candlesticks",
            params=params,
        )
        return data.get("candlesticks", [])

    # ── Convenience: closing yes-price for a list of markets ────────────────

    def get_closing_prices(self, market_tickers: list[str]) -> dict[str, float]:
        """Returns {ticker: yes_close_price_in_dollars}.

        Uses /markets/<ticker> and reads the 'last_price' (cents). Skips
        tickers that 404 or have no last_price.
        """
        out: dict[str, float] = {}
        for t in market_tickers:
            try:
                m = self.get_market(t).get("market", {})
            except Exception as e:
                print(f"  [kalshi] {t}: {e}")
                continue
            cents = m.get("last_price") or m.get("close_price") or m.get("yes_bid")
            if cents is None:
                continue
            out[t] = round(cents / 100.0, 4)
        return out


# ─── Backtest helper: store closing prices keyed by player ───────────────────

def closing_prices_by_player(client: KalshiClient,
                             event_ticker: str,
                             player_to_ticker: dict[str, str]) -> dict[str, float]:
    """Resolves closing yes-prices keyed by player name.

    `player_to_ticker` maps canonical player names to the YES-side market
    ticker for "Will <player> win <event>?". Returns {player: yes_price}.
    """
    tickers = list(player_to_ticker.values())
    by_ticker = client.get_closing_prices(tickers)
    return {
        player: by_ticker[t]
        for player, t in player_to_ticker.items()
        if t in by_ticker
    }


# ─── CLI smoke test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="Kalshi API smoke test.")
    ap.add_argument("--search", type=str, help="search events by query")
    ap.add_argument("--event",  type=str, help="event_ticker — list its markets")
    ap.add_argument("--market", type=str, help="market_ticker — print details")
    ap.add_argument("--env",    type=str, default="prod")
    args = ap.parse_args()

    c = KalshiClient(env=args.env)
    if args.market:
        print(json.dumps(c.get_market(args.market), indent=2))
    elif args.event:
        ev = c.get_event(args.event)
        print(json.dumps(ev, indent=2))
    elif args.search:
        evs = c.search_events(query=args.search, status="open", limit=20)
        for e in evs:
            print(f"{e.get('event_ticker'):<30}  {e.get('title')}")
    else:
        ap.print_help()
