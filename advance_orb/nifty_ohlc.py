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

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import tvscreener as tvs
from tvscreener import StockField, Market

# Wide-table store of all candles for the latest day (1 stock = 1 row).
# One file per timeframe, written by the candle recorder (5-min file) and the
# recorder's 15-min twin (15-min file).  The page reads whichever our own JSON
# store holds for the selected timeframe.
_CANDLES_STORE_5 = Path(__file__).resolve().parent.parent / "stocks" / "orb_candles_5min.json"
_CANDLES_STORE_15 = Path(__file__).resolve().parent.parent / "stocks" / "orb_candles_15min.json"


def _store_path(timeframe: int = 5) -> Path:
    return _CANDLES_STORE_15 if int(timeframe) == 15 else _CANDLES_STORE_5

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
        # Guard the TradingView scanner call — tvscreener exposes no timeout
        # and can hang indefinitely on Render when TradingView throttles us.
        import concurrent.futures as _cf
        _nifty_budget = 25.0
        try:
            with _cf.ThreadPoolExecutor(max_workers=1) as _ex:
                df = _ex.submit(ss.get).result(timeout=_nifty_budget)
        except _cf.TimeoutError:
            self._last_error = f"TradingView snapshot timed out after {_nifty_budget:.0f}s"
            raise RuntimeError(self._last_error)
        df = df[_COLUMNS]

        want = _orb_universe()
        # User rule: keep only stocks whose 9:15 candle CLOSE is above the
        # 200 EMA (same definition as the Advance ORB toggle — 9:15 close vs
        # prior-day EMA, ≤3% cap, fail-open on missing data).
        from advance_orb.common import above_200_ema_symbols
        keep = above_200_ema_symbols(list(want.keys()))

        # Filter the market-wide scan down to just our ORB-universe symbols
        # that pass the 9:15-above-200-EMA rule.
        base_col = df["Symbol"].apply(lambda s: str(s).split(":")[-1].strip().upper())
        mask = base_col.isin(keep)
        df = df[mask].copy()

        rows = []
        for _, r in df.iterrows():
            base = str(r["Symbol"]).split(":")[-1].strip().upper()
            price = r["Price"] or 0.0
            vwap = r["Vwap|5"] or 0.0
            ema = r["Ema200|5"] or 0.0
            change = r["Change %"]
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


def _store_rows(rows: list[dict]) -> int:
    """Persist the forming-candle OHLC for these rows into the JSON file."""
    from advance_orb.candle_recorder import current_candle_label, save_rows
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
    return save_rows(payloads)


def _load_day_candles(path: Path) -> tuple[dict[str, dict], list[str]]:
    """Read the wide candle store -> {symbol: {lbl: {o,h,l,c,vwap,vol,ema,chg}}}, labels.

    Per-candle groups carry Open/High/Low/Close/VWAP/Volume.  The 200 EMA and
    Change % are day-level values (stored once on the 09:15 candle) and are
    repeated onto every candle group of that symbol so the table can show them
    per group.  Falls back to the live-snapshot rows when the store is
    empty/missing (e.g. a fresh day before market opens) so the page never
    shows an empty table.  ``path`` selects the 5-min or 15-min JSON store per
    the dropdown.
    """
    if path.exists():
        try:
            data = json.loads(path.read_text("utf-8"))
        except Exception:
            data = {}
    else:
        data = {}

    by_symbol: dict[str, dict] = {}
    labels: set[str] = set()
    for key, rec in data.items():
        _, sym = (key.split("|", 1) + ["", ""])[:2]
        if not sym:
            continue
        per: dict[str, dict] = {}
        for col, val in rec.items():
            # columns look like price_1300_O  /  vwap_1300  /  volume_1300
            parts = col.split("_")
            if parts[0] == "vwap" and len(parts) == 2:
                per.setdefault(parts[1], {})["vwap"] = val
            elif parts[0] == "volume" and len(parts) == 2:
                per.setdefault(parts[1], {})["vol"] = val
            elif (
                len(parts) == 3
                and parts[0] == "price"
                and parts[2] in ("O", "H", "L", "C")
            ):
                lbl, ohlc = parts[1], parts[2].lower()
                per.setdefault(lbl, {})[ohlc] = val
        # Day-level 200 EMA / change% live on the 09:15 anchor row.
        anchor_ema = rec.get("ema200_0915")
        anchor_chg = rec.get("change_pct_0915")
        # Keep a candle label only when it actually holds an open/close price.
        for lbl, c in list(per.items()):
            if not c.get("o") and not c.get("c"):
                per.pop(lbl, None)
                continue
            c.setdefault("h", None)
            c.setdefault("l", None)
            c.setdefault("vwap", None)
            c.setdefault("vol", None)
            c.setdefault("ema", anchor_ema)
            c.setdefault("chg", anchor_chg)
        if per:
            by_symbol[sym] = per
            labels.update(per.keys())

    ordered = sorted(labels, key=lambda s: int(s)) if labels else []
    return by_symbol, ordered


def build_payload(timeframe: int = 5):
    """Build the full response for the frontend (+ persist to JSON file).

    ``timeframe`` selects which of our own JSON candle stores (5-min or 15-min)
    fills the wide candle table.  The live snapshot rows remain the 5-min ORB
    universe snapshot; only the stored-candle table switches files.
    """
    rows, error = _SERVICE.snapshot()
    stored = _store_rows(rows)
    now = _ist_now()
    store_by_symbol, candle_labels = _load_day_candles(_store_path(timeframe))

    # Attach all stored day-candles to each row (column-wise wide table).
    for r in rows:
        r["candles"] = store_by_symbol.get(r["symbol"], {})

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
        "candle_labels": candle_labels,
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
