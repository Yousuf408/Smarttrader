"""Candle recorder — writes the ORB universe's 5-min candles into Supabase.

Design
------
The ``orb_candles_5min`` table is a wide table: one row per ``(date, symbol)``
with a pair of columns for every 5-minute candle from 09:15 -> 15:10 IST
(``price_<HHMM>_O/H/L/C`` + ``vwap_<HHMM>``), plus the 200-EMA and Change %
only on the 9:15 candle (``ema200_915``, ``change_pct_915``).

Every ~30 s during market hours (Mon–Fri 09:14 -> 15:20 IST) we:
  1. Pull the ORB universe from ``fetch_tradingview_stocks()`` (the exact
     stock list the Advance ORB scan trades — not the watchlist).
  2. Snapshot the whole NSE market via tvscreener in ONE request and keep
     only ours, reading each stock's *forming* 5-min OHLC + VWAP + EMA200 +
     Change%.
  3. Upsert one row per symbol with ``ON CONFLICT (date, symbol) DO UPDATE``
     that only touches the *current* candle's columns. Closed candles are
     never rewritten, so each row accumulates the day's full 5-min picture.

Because the table has no per-candle volume column (per the agreed schema),
volume is intentionally not stored here.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime
from pathlib import Path

from zoneinfo import ZoneInfo

# Local JSON fallback so candles are persisted even if Supabase is unavailable.
_CANDLES_JSON = Path(__file__).resolve().parent.parent / "stocks" / "orb_candles_5min.json"
_JSON_LOCK = threading.Lock()

IST = ZoneInfo("Asia/Kolkata")

# 5-min candle labels: "0915" .. "1510" (column suffix for price_/vwap_).
CANDLE_LABELS: list[str] = []
_m = 9 * 60 + 15
while _m <= 15 * 60 + 10:
    CANDLE_LABELS.append(f"{_m // 60:02d}{_m % 60:02d}")
    _m += 5

_OPEN_IST_MIN = 9 * 60 + 15
_CLOSE_IST_MIN = 15 * 60 + 30
_RECORD_START_MIN = 9 * 60 + 13   # a touch early so the 09:15 candle is caught
_RECORD_END_MIN = 15 * 60 + 20    # after the last (15:10) candle closes at 15:15
_POLL_S = 30.0
_IDLE_S = 60.0

# Single-flight across the whole recorder (only one fetch round at a time).
_rec_lock = threading.Lock()


def current_candle_label(now: datetime | None = None) -> str | None:
    """Label of the currently-forming 5-min candle, or None outside a candle.

    The 09:15 candle spans 09:15:00–09:19:59 and closes at 09:20:00, so the
    forming label is ``minute // 5 * 5`` rounded down to the nearest 5-min
    boundary that falls on one of our candle labels.
    """
    now = now or datetime.now(IST)
    minutes = now.hour * 60 + now.minute
    if minutes < _OPEN_IST_MIN or minutes > _CLOSE_IST_MIN:
        return None
    lbl = f"{(minutes // 5 * 5) // 60:02d}{(minutes // 5 * 5) % 60:02d}"
    return lbl if lbl in CANDLE_LABELS else None


# ── Status store (read by GET /api/candles/status) ──
def _fresh_status() -> dict:
    return {
        "running": False,
        "last_tick": None,
        "last_candle": None,
        "today": None,
        "universe": 0,
        "matched": 0,
        "saved": 0,
        "errors": 0,
        "last_message": None,
    }


_rec_status = _fresh_status()
_status_lock = threading.Lock()


def _set_status(**kw):
    with _status_lock:
        _rec_status.update(kw)


def get_status() -> dict:
    with _status_lock:
        return dict(_rec_status)


# ── ORB universe ────────────────────────────────────────────────
def universe_symbols() -> list[str]:
    """Names of the stocks the Advance-ORB scan currently returns (TV only)."""
    from advance_orb.common import fetch_tradingview_stocks
    rows = fetch_tradingview_stocks()
    return [str(r["name"]).strip().upper() for r in rows if r.get("name")]


# ── Market snapshot (one request for the entire universe) ───────
_SS_FIELDS = None
_SS_COLUMNS = None


def _snapshot_fields():
    global _SS_FIELDS, _SS_COLUMNS
    if _SS_FIELDS is None:
        import tvscreener as tvs
        from tvscreener import StockField, Market  # noqa: F401 (market used)
        _SS_FIELDS = [
            StockField.NAME, StockField.PRICE,
            StockField.OPEN_5, StockField.HIGH_5, StockField.LOW_5,
            StockField.CLOSE_5, StockField.VWAP_5,
            StockField.EMA200_5, StockField.CHANGE_PERCENT,
        ]
        _SS_COLUMNS = [
            "Symbol", "Name", "Price", "Open|5", "High|5", "Low|5",
            "Close|5", "Vwap|5", "Ema200|5", "Change %",
        ]
    return _SS_FIELDS, _SS_COLUMNS


def snapshot_rows() -> dict[str, dict]:
    """One tvscreener scan -> {base_symbol: {o,h,l,c,vwap,ema,change}}.

    Skips symbols with no usable OHLC. Returns only rows we can store.
    """
    import tvscreener as tvs
    from tvscreener import Market
    fields, columns = _snapshot_fields()
    ss = tvs.StockScreener()
    ss.set_markets(Market.INDIA)
    ss.specific_fields = fields
    ss.set_range(0, 800)  # wide enough to cover the ORB universe (mcap-desc)
    df = ss.get()
    if df is None or df.empty:
        return {}
    try:
        df = df[columns]
    except (KeyError, ValueError):
        return {}

    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        sym_full = r["Symbol"]
        base = str(sym_full).split(":")[-1].strip().upper() if sym_full else ""
        if not base:
            continue
        try:
            o = float(r["Open|5"]); h = float(r["High|5"])
            lo = float(r["Low|5"]); c = float(r["Close|5"])
        except (TypeError, ValueError):
            continue
        if None in (o, h, lo, c) or h == 0:
            continue
        ema = float(r["Ema200|5"]) if r["Ema200|5"] is not None else None
        vwap = float(r["Vwap|5"]) if r["Vwap|5"] is not None else None
        change = float(r["Change %"]) if r["Change %"] is not None else None
        # NOTE: the "above 200 EMA" filter is applied at the payload layer via
        # common.above_200_ema_symbols() using the 9:15 CANDLE CLOSE vs the
        # prior-day EMA (same definition as the Advance ORB toggle). The live
        # Ema200|5 here is only carried for the ema200_915 column, never used
        # as a filter.
        row = {
            "o": o, "h": h, "l": lo, "c": c,
            "vwap": vwap, "ema": ema, "change": change,
        }
        out[base] = row
    return out


# ── JSON fallback storage (always-on, Supabase-independent) ─────
def _json_reead() -> dict:
    if not _CANDLES_JSON.exists():
        return {}
    try:
        return json.loads(_CANDLES_JSON.read_text("utf-8"))
    except Exception:
        return {}


def _json_upsert(rows: list[dict]) -> int:
    """Merge rows into the local JSON file keyed by ``date|symbol``.

    Role: the file holds ONLY the latest trading day's rows.  On the first
    write of a new day, all rows with a different ``date`` are dropped so the
    file always reflects the current day's universe — each morning it starts
    fresh for that day's new stocks.
    """
    if not rows:
        return 0
    today = rows[0].get("date")
    with _JSON_LOCK:
        data = _json_reead()
        if today:
            # Prune the previous day(s) so only today's candle data survives.
            data = {k: v for k, v in data.items() if k.split("|")[0] == today}
        for r in rows:
            key = f"{r.get('date')}|{r.get('symbol')}"
            rec = data.get(key) or {}
            rec.update(r)          # merge only the fields present this tick
            rec.setdefault("date", r.get("date"))
            rec.setdefault("symbol", r.get("symbol"))
            data[key] = rec
        tmp = _CANDLES_JSON.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(_CANDLES_JSON)
    return len(rows)


# ── Storage ─────────────────────────────────────────────────────
def save_rows(rows: list[dict]) -> int:
    """Persist the candle rows to the local JSON file (Supabase disabled)."""
    return _json_upsert(rows)


# ── Single tick ─────────────────────────────────────────────────
def record_once() -> dict:
    """Fetch the universe + snapshot and PATCH the forming-candle columns."""
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    lbl = current_candle_label(now)

    if lbl is None:
        return {"saved": 0, "candle": None, "message": f"{today} — outside candle window"}

    with _rec_lock:
        universe = universe_symbols()
        if not universe:
            _set_status(running=True, last_tick=today, last_candle=lbl,
                        today=today, universe=0, matched=0, saved=0,
                        errors=0, last_message="no ORB universe from TradingView")
            return {"saved": 0, "candle": lbl, "message": "empty universe"}

        # User rule: keep only stocks whose 9:15 candle CLOSE is above the
        # 200 EMA (same definition as the Advance ORB toggle — 9:15 close vs
        # prior-day EMA, ≤3% cap, fail-open on missing data).
        from advance_orb.common import above_200_ema_symbols
        keep = above_200_ema_symbols(universe)

        snap = snapshot_rows()
        matched = 0
        payloads: list[dict] = []
        for base in universe:
            if base not in keep:
                continue
            row = snap.get(base)
            if not row:
                continue
            matched += 1
            p = {
                "date": today,
                "symbol": base,
                f"price_{lbl}_O": row["o"],
                f"price_{lbl}_H": row["h"],
                f"price_{lbl}_L": row["l"],
                f"price_{lbl}_C": row["c"],
                f"vwap_{lbl}": row["vwap"],
            }
            # EMA200 + Change% are stored only on the 9:15 candle.
            if lbl == "0915":
                p["ema200_915"] = row["ema"]
                p["change_pct_915"] = row["change"]
            payloads.append(p)

        saved = errors = 0
        if payloads:
            saved = save_rows(payloads)

        msg = f"{today} {lbl}: {saved}/{len(payloads)} rows"
        _set_status(running=True, last_tick=f"{now.strftime('%H:%M:%S')} {today}",
                    last_candle=lbl, today=today, universe=len(universe),
                    matched=matched, saved=saved, errors=errors, last_message=msg)
        return {"saved": saved, "candle": lbl, "message": msg}


# ── Background loop ─────────────────────────────────────────────
async def candle_recorder_loop():
    """Run during market hours; idle-poll otherwise."""
    _set_status(running=True)
    while True:
        try:
            now = datetime.now(IST)
            is_weekday = now.weekday() < 5
            minutes = now.hour * 60 + now.minute
            if is_weekday and _RECORD_START_MIN <= minutes <= _RECORD_END_MIN:
                await asyncio.to_thread(record_once)
                await asyncio.sleep(_POLL_S)
            else:
                await asyncio.sleep(_IDLE_S)
        except asyncio.CancelledError:
            _set_status(running=False)
            raise
        except Exception as exc:  # noqa: BLE001
            _set_status(running=True, errors=1, last_message=str(exc)[:200])
            await asyncio.sleep(_POLL_S)
