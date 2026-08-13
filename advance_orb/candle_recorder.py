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

# Local JSON files so candles are persisted without any external DB.
_CANDLES_JSON = Path(__file__).resolve().parent.parent / "stocks" / "orb_candles_5min.json"
_CANDLES_15_JSON = Path(__file__).resolve().parent.parent / "stocks" / "orb_candles_15min.json"
_JSON_LOCK = threading.Lock()

# Frozen ORB universe per trading day: captured ONCE at the first tick of the
# day, then reused for the whole session so a stock keeps being recorded even
# after it drops out of the live scan mid-day (gives full candle series).
_DAY_FROZEN_UNIVERSE: dict[str, list[str]] = {}

IST = ZoneInfo("Asia/Kolkata")


def _make_labels(start_min: int, end_min: int, step: int) -> list[str]:
    """Column-suffix labels (e.g. '0915') from start to end at a given step."""
    out: list[str] = []
    m = start_min
    while m <= end_min:
        out.append(f"{m // 60:02d}{m % 60:02d}")
        m += step
    return out


# 5-min candle labels: "0915" .. "1510" (column suffix for price_/vwap_).
CANDLE_LABELS: list[str] = _make_labels(9 * 60 + 15, 15 * 60 + 10, 5)

# 15-min candle labels: "0915" .. "1500" (column suffix for price_/vwap_).
CANDLE_LABELS_15: list[str] = _make_labels(9 * 60 + 15, 15 * 60, 15)

_OPEN_IST_MIN = 9 * 60 + 15
_CLOSE_IST_MIN = 15 * 60 + 30
_RECORD_START_MIN = 9 * 60 + 13   # a touch early so the 09:15 candle is caught
_RECORD_END_MIN = 15 * 60 + 20    # after the last (15:10) candle closes at 15:15
_POLL_S = 30.0
_IDLE_S = 60.0

# Single-flight across the whole recorder (only one fetch round at a time).
_rec_lock = threading.Lock()


def _prev_candle_label(lbl: str | None, labels: list[str]) -> str | None:
    """Return the label immediately before ``lbl`` in ``labels``, or None.

    Used to identify the just-closed candle so its true close can be written.
    """
    if lbl is None:
        return None
    try:
        idx = labels.index(lbl)
        return labels[idx - 1] if idx > 0 else None
    except ValueError:
        return None


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


def current_candle_label_15(now: datetime | None = None) -> str | None:
    """Label of the currently-forming 15-min candle (0915 -> 1500), or None."""
    now = now or datetime.now(IST)
    minutes = now.hour * 60 + now.minute
    if minutes < _OPEN_IST_MIN or minutes > _CLOSE_IST_MIN:
        return None
    lbl = f"{(minutes // 15 * 15) // 60:02d}{(minutes // 15 * 15) % 60:02d}"
    return lbl if lbl in CANDLE_LABELS_15 else None


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
            StockField.OPEN_15, StockField.HIGH_15, StockField.LOW_15,
            StockField.CLOSE_15, StockField.VWAP_15, StockField.VOLUME_15,
            StockField.EMA200_5, StockField.CHANGE_PERCENT,
        ]
        _SS_COLUMNS = [
            "Symbol", "Name", "Price", "Open|5", "High|5", "Low|5",
            "Close|5", "Vwap|5",
            "Open|15", "High|15", "Low|15", "Close|15", "Vwap|15", "Volume|15",
            "Ema200|5", "Change %",
        ]
    return _SS_FIELDS, _SS_COLUMNS


def snapshot_rows() -> dict[str, dict]:
    """One tvscreener scan -> {base_symbol: {o,h,l,c,vwap,ema,change,o15,h15,l15,c15,vwap15}}.

    Skips symbols with no usable OHLC. Returns only rows we can store.
    Both the 5-min and 15-min forming candles come from the same scan.
    """
    import tvscreener as tvs
    from tvscreener import Market
    fields, columns = _snapshot_fields()
    ss = tvs.StockScreener()
    ss.set_markets(Market.INDIA)
    ss.specific_fields = fields
    ss.set_range(0, 3000)  # cover the whole ORB universe (mcap-desc) so every stock gets candles
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
        # 15-min forming candle (fail-open -> None if TV returns nothing).
        try:
            o15 = float(r["Open|15"]) if r["Open|15"] is not None else None
            h15 = float(r["High|15"]) if r["High|15"] is not None else None
            l15 = float(r["Low|15"]) if r["Low|15"] is not None else None
            c15 = float(r["Close|15"]) if r["Close|15"] is not None else None
            v15 = float(r["Vwap|15"]) if r["Vwap|15"] is not None else None
            vol15 = float(r["Volume|15"]) if r["Volume|15"] is not None else None
        except (TypeError, ValueError):
            o15 = h15 = l15 = c15 = v15 = vol15 = None
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
            "o15": o15, "h15": h15, "l15": l15, "c15": c15, "vwap15": v15,
            "vol15": vol15,
        }
        out[base] = row
    return out


# ── JSON fallback storage (always-on, Supabase-independent) ─────
def _json_reead(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def _json_upsert(rows: list[dict], path: Path) -> int:
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
        data = _json_reead(path)
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
        # Count summary pinned at the very top of the file.
        stock_count = sum(1 for k in data if k.split("|")[0] == today)
        meta = {"stock_count": stock_count, "date": today}
        data = {"__meta__": meta, **data}
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(path)
    return len(rows)


# ── Storage ─────────────────────────────────────────────────────
def save_rows(rows: list[dict]) -> int:
    """Persist the candle rows to the 5-min local JSON file."""
    return _json_upsert(rows, _CANDLES_JSON)


def save_rows_15(rows: list[dict]) -> int:
    """Persist the candle rows to the 15-min local JSON file."""
    return _json_upsert(rows, _CANDLES_15_JSON)


def load_orb_candles_9_15(timeframe: int = 15) -> dict[str, dict]:
    """Read the opening 9:15 candle (+ the 2nd candle for the inside check)
    straight from our own TradingView JSON store.

    Returns ``{symbol: {open915, high915, low915, close915, close920,
    inside_915, c2_close, c3_close, c4_close, ema200}}`` or ``{}`` when the
    store is missing/empty.  The follow-up candles (2nd/3rd/4th bars after the
    09:15 open) are 09:20/09:25/09:30 on 5-min and 09:30/09:45/10:00 on 15-min,
    so the "3 Candles Inside 9:15" filter can be computed from the store too.
    """
    tf = int(timeframe)
    path = _CANDLES_15_JSON if tf == 15 else _CANDLES_JSON
    if tf == 15:
        c2l, c3l, c4l = "0930", "0945", "1000"
    else:
        c2l, c3l, c4l = "0920", "0925", "0930"
    data = _json_reead(path)
    out: dict[str, dict] = {}
    for key, rec in data.items():
        _, sym = (key.split("|", 1) + ["", ""])[:2]
        if not sym:
            continue
        o = rec.get("price_0915_O")
        h = rec.get("price_0915_H")
        l = rec.get("price_0915_L")
        c = rec.get("price_0915_C")
        c2 = rec.get(f"price_{c2l}_C")
        c3 = rec.get(f"price_{c3l}_C")
        c4 = rec.get(f"price_{c4l}_C")
        if o is None or h is None or l is None or c is None:
            continue  # 9:15 bar not complete yet for this symbol
        inside = None
        if c2 is not None and h > 0 and l is not None:
            inside = bool(float(l) <= float(c2) <= float(h))
        out[sym] = {
            "open915": o, "high915": h, "low915": l, "close915": c,
            "close920": c2, "inside_915": inside,
            "c2_close": c2, "c3_close": c3, "c4_close": c4,
            "ema200": rec.get("ema200_0915"),
        }
    return out


def load_orb_candles_both() -> dict[str, dict]:
    """Merge the 5-min and 15-min ORB candle stores into one per-symbol map.

    For each symbol we use whichever store has the data — both files are read
    and missing fields are filled from the other timeframe.  This is the
    single source for Big Players and the Breakout table so they keep working
    even when CandleTracker has not completed its 9:15 slot yet (relying on
    ``batch_opening_candle`` left those tables empty early in the session).

    Returns ``{symbol: {open915, high915, low915, close915, close920,
    ema200, day_low}}``.
    """
    def _from_rec(rec: dict, ema_key: str) -> dict:
        lows: list[float] = []
        for k, v in rec.items():
            if k.startswith("price_") and k.endswith("_L") and isinstance(v, (int, float)):
                lows.append(float(v))
        return {
            "open915": rec.get("price_0915_O"),
            "high915": rec.get("price_0915_H"),
            "low915": rec.get("price_0915_L"),
            "close915": rec.get("price_0915_C"),
            # 2nd candle close: 09:20 on 5-min, 09:30 on 15-min.
            "close920": rec.get("price_0920_C", rec.get("price_0930_C")),
            "ema200": rec.get(ema_key) or rec.get("ema200_0915") or rec.get("ema200_915"),
            "day_low": min(lows) if lows else None,
        }

    merged: dict[str, dict] = {}
    for key, rec in _json_reead(_CANDLES_JSON).items():
        _, sym = (key.split("|", 1) + ["", ""])[:2]
        if not sym or rec.get("price_0915_H") is None:
            continue
        merged[sym] = _from_rec(rec, ema_key="ema200_915")
    for key, rec in _json_reead(_CANDLES_15_JSON).items():
        _, sym = (key.split("|", 1) + ["", ""])[:2]
        if not sym or rec.get("price_0915_H") is None:
            continue
        facts = _from_rec(rec, ema_key="ema200_0915")
        if sym not in merged:
            merged[sym] = facts
        else:
            # Fill any gaps from the other timeframe's store.
            for k, v in facts.items():
                if merged[sym].get(k) is None and v is not None:
                    merged[sym][k] = v
    return merged


# ── Single tick ─────────────────────────────────────────────────
def record_once() -> dict:
    """Fetch the universe + snapshot and PATCH the forming-candle columns."""
    now = datetime.now(IST)
    today = now.strftime("%Y-%m-%d")
    lbl = current_candle_label(now)
    lbl15 = current_candle_label_15(now)

    if lbl is None and lbl15 is None:
        return {"saved": 0, "candle": None, "message": f"{today} — outside candle window"}

    with _rec_lock:
        # Freeze the ORB scan-list once per trading day.  Once captured, the
        # same stock set is recorded the whole day, so a stock that leaves the
        # live scan mid-session still gets a full candle series.
        if today not in _DAY_FROZEN_UNIVERSE or not _DAY_FROZEN_UNIVERSE[today]:
            _DAY_FROZEN_UNIVERSE[today] = universe_symbols()
        universe = _DAY_FROZEN_UNIVERSE[today]
        if not universe:
            _set_status(running=True, last_tick=today, last_candle=lbl,
                        today=today, universe=0, matched=0, saved=0,
                        errors=0, last_message="no ORB universe from TradingView")
            return {"saved": 0, "candle": lbl, "message": "empty universe"}

        snap = snapshot_rows()
        matched = 0
        payloads: list[dict] = []
        payloads15: list[dict] = []
        for base in universe:
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

            if lbl15 is not None:
                # 15-min candle columns (only present when a 15-min candle can be
                # forming). Requires the 15-min OHLC to actually be returned.
                c15 = row.get("c15")
                if c15 is not None:
                    p15 = {
                        "date": today,
                        "symbol": base,
                        f"price_{lbl15}_O": row.get("o15"),
                        f"price_{lbl15}_H": row.get("h15"),
                        f"price_{lbl15}_L": row.get("l15"),
                        f"price_{lbl15}_C": c15,
                        f"vwap_{lbl15}": row.get("vwap15"),
                        f"volume_{lbl15}": row.get("vol15"),
                    }
                    # EMA200 + Change% stored only on the 09:15 candle.
                    if lbl15 == "0915":
                        p15["ema200_0915"] = row["ema"]
                        p15["change_pct_0915"] = row["change"]
                    payloads15.append(p15)

        saved = errors = 0
        if payloads:
            saved = save_rows(payloads)
        if payloads15:
            save_rows_15(payloads15)

        # ── True-close backfill for the 2nd candle (inside-9:15 fix) ───────
        # The forming-candle snapshot above stores the provisional close (as of
        # the last ~30s poll), which may miss a last-second breakout.  Once the
        # 2nd candle is fully sealed, batch_tv_confirmed_c2_close fetches the
        # authoritative finalized close from the TV chart historical series (NOT
        # the forming-bar screener field) and writes it into the JSON store so
        # load_orb_candles_9_15 computes inside_915 from the correct bar close.
        #
        # batch_tv_confirmed_c2_close only runs when past the 2nd candle's
        # close time (09:25 for 5-min, 09:45 for 15-min) and uses _TV_C2_CACHE
        # (separate from the opening-candle cache) so a pre-close screener call
        # can never poison the store with a provisional value.
        try:
            from advance_orb.tv_chart_candles import batch_tv_confirmed_c2_close
            if lbl is not None:
                tv5 = batch_tv_confirmed_c2_close(universe, timeframe=5)
                bf5: list[dict] = [
                    {"date": today, "symbol": sym, "price_0920_C": float(c2c)}
                    for sym, c2c in tv5.items()
                    if c2c is not None
                ]
                if bf5:
                    save_rows(bf5)
            if lbl15 is not None:
                tv15 = batch_tv_confirmed_c2_close(universe, timeframe=15)
                bf15: list[dict] = [
                    {"date": today, "symbol": sym, "price_0930_C": float(c2c)}
                    for sym, c2c in tv15.items()
                    if c2c is not None
                ]
                if bf15:
                    save_rows_15(bf15)
        except Exception as exc:  # noqa: BLE001
            # Never let a TV chart fetch failure break the main recorder.
            print(f"[candles] c2-close backfill failed: {exc}")

        msg = f"{today} {lbl}: {saved}/{len(payloads)} rows (+15m {len(payloads15)})"
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
