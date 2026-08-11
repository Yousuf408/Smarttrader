"""TradingView OHLC page — all ORB-universe stocks, stored to Supabase.

Fetches 5-minute OHLC + VWAP + 200 EMA + volume + daily change for the exact
stocks the Advance ORB scan pulls from TradingView (the ORB universe), using
the third-party ``tvscreener`` library (v0.4.0 API).  Returns a JSON payload
for the frontend and stores each stock's forming 5-min candle into the
existing ``orb_candles_5min`` table (one row per ``(date, symbol)``).

Notes on the tvscreener 0.4.0 API (they differ from older tutorials / the
original ``tvscreener`` blog post):
  * There is no ``StockField.SYMBOL``; use ``StockField.NAME``/``StockField.PRICE``
    and read the ``Symbol`` column from the result dataframe.
  * 5-minute fields are suffixed: ``OPEN_5``, ``HIGH_5``, ``LOW_5``, ``CLOSE_5``,
    ``VOLUME_5``, ``VWAP_5``, ``EMA200_5`` (there is no ``with_interval("5")``).
  * There is no symbol-list ``isin`` filter (it maps to ``in_range`` which
    TradingView rejects with a list). So we scope to ``Market.INDIA`` sorted by
    market cap (default) and filter the resulting rows to our symbol set in
    pandas. The ORB universe (mcap-desc screen) falls well within range 0-800.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import tvscreener as tvs
from tvscreener import StockField, Market

# TradingView field set for the 5-minute interval.
_FIELDS = [
    StockField.NAME,
    StockField.PRICE,
    StockField.OPEN_5,
    StockField.HIGH_5,
    StockField.LOW_5,
    StockField.CLOSE_5,
    StockField.VOLUME_5,
    StockField.VWAP_5,
    StockField.EMA200_5,
    StockField.CHANGE_PERCENT,
]

_COLUMNS = [
    "Symbol", "Name", "Price", "Open|5", "High|5", "Low|5",
    "Close|5", "Volume|5", "Vwap|5", "Ema200|5", "Change %",
]

# NSE equity cash session (IST): 09:15 -> 15:30, Mon-Fri.
_OPEN_MIN = 9 * 60 + 15
_CLOSE_MIN = 15 * 60 + 30


def _ist_now():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def _market_status(now):
    """Return (open: bool, label: str, next_label: str) for the running view."""
    minutes = now.hour * 60 + now.minute
    is_weekday = now.weekday() < 5
    is_open = is_weekday and _OPEN_MIN <= minutes <= _CLOSE_MIN
    if is_open:
        return True, "Open", "Market is open"
    if not is_weekday:
        return False, "Closed", "Market is closed (weekend)"
    if minutes < _OPEN_MIN:
        return False, "Pre-market", f"Opens at 09:15 IST"
    return False, "Closed", "Market closed for the day"


def _orb_universe() -> dict[str, dict]:
    """Base-symbol -> display data for the ORB universe (TV, not watchlist)."""
    from advance_orb.common import fetch_tradingview_stocks
    rows = fetch_tradingview_stocks()
    out: dict[str, dict] = {}
    for r in rows:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        base = name.split(":")[-1].strip().upper()
        if base:
            out[base] = {"name": name}
    return out


class ORBOHLCService:
    """Cached TradingView snapshot of every ORB-universe stock."""

    def __init__(self, ttl_seconds: float = 20.0):
        self.ttl = ttl_seconds
        self._lock = threading.Lock()
        self._cache = None      # list[dict]
        self._cached_at = 0.0
        self._last_error = None

    def _fetch(self):
        ss = tvs.StockScreener()
        ss.set_markets(Market.INDIA)
        ss.specific_fields = _FIELDS
        ss.set_range(0, 800)             # covers the ORB universe (mcap-desc)
        df = ss.get()
        df = df[_COLUMNS]

        want = _orb_universe()
        # Filter the market-wide scan down to just our ORB universe symbols.
        base_col = df["Symbol"].apply(lambda s: str(s).split(":")[-1].strip().upper())
        mask = base_col.isin(want.keys())
        df = df[mask].copy()

        rows = []
        for _, r in df.iterrows():
            base = str(r["Symbol"]).split(":")[-1].strip().upper()
            price = r["Price"] or 0.0
            vwap = r["Vwap|5"] or 0.0
            ema = r["Ema200|5"] or 0.0
            change = r["Change %"]
            # User rule: keep only stocks trading ABOVE their 200 EMA.
            if ema is None or price <= ema:
                continue
            rows.append({
                "symbol": base,
                "name": str(r["Name"]) or want.get(base, {}).get("name", base),
                "price": round(price, 2),
                "open": round(r["Open|5"], 2) if r["Open|5"] is not None else None,
                "high": round(r["High|5"], 2) if r["High|5"] is not None else None,
                "low": round(r["Low|5"], 2) if r["Low|5"] is not None else None,
                "close": round(r["Close|5"], 2) if r["Close|5"] is not None else None,
                "vwap": round(vwap, 2),
                "ema200": round(ema, 2),
                "volume": int(r["Volume|5"]) if r["Volume|5"] is not None else 0,
                "change_pct": round(change, 2) if change is not None else 0.0,
            })

        rows.sort(key=lambda x: x["symbol"])
        self._cache = rows
        self._cached_at = time.time()
        self._last_error = None
        return rows

    def snapshot(self):
        """Return the latest rows (cache-busting no data-freshness staleness)."""
        with self._lock:
            if self._cache is not None and (time.time() - self._cached_at) < self.ttl:
                rows = self._cache
            else:
                try:
                    rows = self._fetch()
                except Exception as exc:  # noqa: BLE001 - surface via payload
                    self._last_error = str(exc)
                    if self._cache is not None:
                        rows = self._cache
                    else:
                        rows = []
            return rows, self._last_error


_SERVICE = ORBOHLCService(ttl_seconds=20.0)


def _store_to_supabase(rows: list[dict]) -> int:
    """Persist the forming-candle OHLC for these rows into orb_candles_5min."""
    from advance_orb.candle_recorder import current_candle_label, _supabase_upsert
    lbl = current_candle_label(_ist_now())
    if not lbl or not rows:
        return 0
    now = _ist_now()
    today = now.strftime("%Y-%m-%d")
    payloads = []
    for r in rows:
        if r["open"] is None or r["high"] is None or r["low"] is None or r["close"] is None:
            continue
        p = {
            "date": today,
            "symbol": r["symbol"],
            f"price_{lbl}_O": r["open"],
            f"price_{lbl}_H": r["high"],
            f"price_{lbl}_L": r["low"],
            f"price_{lbl}_C": r["close"],
        }
        if r["vwap"]:
            p[f"vwap_{lbl}"] = r["vwap"]
        if lbl == "0915":
            if r["ema200"]:
                p["ema200_915"] = r["ema200"]
            if r["change_pct"]:
                p["change_pct_915"] = r["change_pct"]
        payloads.append(p)
    if not payloads:
        return 0
    try:
        return _supabase_upsert(payloads)
    except Exception as exc:  # noqa: BLE001 - logged, non-fatal to the page
        print(f"[nifty_ohlc] store error: {exc}")
        return 0


def build_payload():
    """Build the full response for the frontend (+ persist to Supabase)."""
    rows, error = _SERVICE.snapshot()
    stored = _store_to_supabase(rows)
    now = _ist_now()
    open_flag, status_label, status_note = _market_status(now)

    gainer = max(rows, key=lambda r: r["change_pct"]) if rows else None
    loser = min(rows, key=lambda r: r["change_pct"]) if rows else None
    above_ema = [r for r in rows if r["ema200"] and r["price"] > r["ema200"]]

    return {
        "as_of": now.isoformat(timespec="seconds"),
        "as_of_display": now.strftime("%d %b %Y, %I:%M:%S %p"),
        "market": {"open": open_flag, "label": status_label, "note": status_note},
        "refresh_seconds": 30,
        "rows": rows,
        "stored": stored,
        "stats": {
            "gainer": gainer,
            "loser": loser,
            "above_ema": {
                "count": len(above_ema),
                "total": len(rows),
                "symbols": [r["symbol"] for r in above_ema],
            },
        },
        "error": error,
    }
