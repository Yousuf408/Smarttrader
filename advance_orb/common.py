"""
Shared constants and helper functions used by Advance ORB and Big Players strategies.
"""

from zoneinfo import ZoneInfo
import datetime
import json
import logging
import threading
import time
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger("advance_orb")

from server.candle_tracker import candle_tracker

from broker.quantity_calculator import (
    calculate_max_quantity_column,
    _cred as _broker_cred,
)
from broker.angel_margin_calculator import (
    is_connected as angel_is_connected,
    calculate_quantities as angel_calculate_quantities,
)

# ─── HARDCODED CONDITIONS ───
PRICE_MIN = 200
PRICE_MAX = 4000  # 200 to 4000 INR per user's old-condition spec
GAP_THRESHOLD = 2.0
MARKET_CAP_MIN = 41_000_000_000  # 41 Billion INR
SMALL_CANDLE_THRESHOLD = 1.5

# ── 200-period EMA (5-min closes, Yahoo Finance) ──
EMA_SPAN = 200
EMA_LOOKBACK_DAYS = 4
ABOVE_EMA_MAX_GAP = 4.0   # "Above 200 EMA" toggle: open at most 4% above EMA

IST = ZoneInfo("Asia/Kolkata")
MAX_TV_STOCKS = 100

# Reduced from 8 to 4 workers — Render free tier (~0.5 CPU), 8 concurrent
# yfinance downloads exhaust the CPU quota and the whole request times out.
# 4 still parallelizes enough while leaving headroom for other server work.
YFINANCE_WORKERS = 4

# Hard cap on how long a single Yahoo batch may run before we bail out and
# return whatever was already fetched.  Render free tier's request timeout
# (~55s) means a 600-symbol batch can blow past it.  Returning partial data
# gracefully + showing the full TV universe beats a 500/timeout error.
YAHOO_BATCH_TIMEOUT = 25.0

# ── TradingView scanner (Advance ORB universe) ─────────────────────
# Moved to the dedicated shared module `tradingview/tv_stocks_filters.py`.
# Re-exported here so existing callers
# (`from advance_orb.common import fetch_tradingview_stocks`) keep working
# unchanged during the refactor — the behavior and returned columns are
# identical to the original implementation.
from tradingview.tv_stocks_filters import (
    fetch_tradingview_stocks,
    TV_SCAN_URL,
    TV_SCAN_TTL,
)


def compute_200_ema(symbol: str):
    """200-period EMA on 5-min closes.  Tries CandleTracker first."""
    if not symbol or not symbol.strip():
        return None
    # Try tracker first (may fall back to yfinance internally)
    return candle_tracker.get_200_ema(symbol.upper().replace(".NS", ""))


def compute_200_ema_batch(symbols: list[str]) -> dict:
    """Batch EMA via CandleTracker (fast path) + yfinance fallback."""
    results: dict = {}
    unique = list({s for s in symbols if s})
    if not unique:
        return results
    # Tracker handles caching and fallback per-symbol
    for sym in unique:
        results[sym] = candle_tracker.get_200_ema(sym.upper().replace(".NS", ""))
    return results


def _detect_trading_date() -> datetime.date | None:
    """Check if today is a live trading day by probing one stock's data."""
    try:
        probe = yf.download(
            tickers="^NSEI",
            period="10d",
            interval="5m",
            progress=False,
            auto_adjust=False,
            prepost=False,
            threads=False,
        )
        if probe is None or probe.empty:
            return None
        if isinstance(probe.columns, pd.MultiIndex):
            probe = probe.xs("^NSEI", axis=1, level=-1)
        idx = pd.DatetimeIndex(probe.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC").tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)
        probe.index = idx
        today = pd.Timestamp.now(tz=IST).date()
        today_data = probe[(probe.index.date == today) &
                           (probe.index.hour == 9) & (probe.index.minute >= 15)]
        if not today_data.empty:
            return today
        all_dates = sorted({
            d for d in set(probe.index.date)
            if len(probe[(probe.index.date == d) &
                         (probe.index.hour == 9) & (probe.index.minute >= 15)]) > 0
        }, reverse=True)
        return all_dates[0] if all_dates else None
    except Exception:
        return None


# ── Market-closed anchor-date resolution ─────────────────────────
# On weekends / holidays / after-hours there are no bars "today", so the
# screener used to return 0 stocks and hammer yfinance.  Instead we anchor
# every Yahoo fact to the most recent trading day and show that data with a
# "market closed" banner.  The probe result is cached so we don't re-probe
# ^NSEI on every request.
_REF_DATE_LOCK = threading.Lock()
_REF_DATE_CACHE: dict = {"at": 0.0, "anchor": None, "is_live": False}
_REF_DATE_TTL = 120.0


def _resolve_reference_date() -> tuple[datetime.date, bool]:
    """Return (anchor_date, is_live_trading_day).

    Live day = today has a 9:15 IST bar (probe).  Otherwise the anchor is
    the most recent date in the probe's history that had a 9:15 bar — i.e.
    the last working day — so weekend/holiday/after-hours runs still show
    real (yesterday's) data instead of an empty table.
    """
    today = datetime.datetime.now(IST).date()
    with _REF_DATE_LOCK:
        if time.time() - _REF_DATE_CACHE["at"] < _REF_DATE_TTL:
            return _REF_DATE_CACHE["anchor"], _REF_DATE_CACHE["is_live"]
        detected = _detect_trading_date()
        if detected is None:
            # Probe failed; fall back to today (existing behavior).
            anchor, is_live = today, False
        else:
            anchor, is_live = detected, (detected == today)
        _REF_DATE_CACHE.update({"at": time.time(), "anchor": anchor, "is_live": is_live})
        return anchor, is_live


def batch_opening_candle(symbols: list[str]) -> dict:
    """Return 9:15 IST candle data per symbol.

    Reads from CandleTracker (WebSocket-built) only.  No yfinance
    fallback — it's too slow for 700+ stocks and its high/low values
    are inaccurate, which was the whole reason for switching to WS.
    If CandleTracker hasn't completed a 9:15 slot yet, all symbols
    return None candle data (table shows price/gap%, candle fields
    fill in once the first 5-min boundary triggers).
    """
    unique = [s for s in {s for s in symbols if s}]
    if not unique:
        return {}

    today_str = datetime.datetime.now(IST).strftime("%Y-%m-%d")
    tracker = candle_tracker
    has_today_data = (
        today_str in tracker.completed
        and 0 in tracker.completed[today_str]
    )
    if has_today_data:
        return tracker.get_candle_data_batch(unique)

    # No CandleTracker data yet — return empty candles for all symbols.
    # Candle data (9:15 high/low, 9:20 close) will populate once the
    # first 5-min boundary completes.  The table can still show
    # Price/GAP%/EMA from WS + cache.
    empty = (False, None, None, None, None, None, None, None, None, None)
    return {s: empty for s in unique}


# ── Yahoo Finance ORB candle data (independent of the WebSocket) ────# ── Yahoo Finance ORB candle data (independent of the WebSocket) ────
# Used by the "Yahoo Filter" toggle on Advance ORB: today's 9:15 + 9:20
# candles and yesterday's high are fetched straight from Yahoo Finance.
# Cached briefly so the frontend's auto-refresh doesn't hammer Yahoo.
_YF_ORB_CACHE: dict[str, tuple[float, dict | None]] = {}
_YF_ORB_CACHE_TTL = 300.0   # 5-min cache for successful fetches
_YF_ORB_FAIL_TTL = 60.0     # failed fetches retried after 1 min
_YF_ORB_LOCK = threading.Lock()


def fetch_yahoo_orb_data(symbol: str, timeframe: int = 5) -> dict | None:
    """Fetch today's opening candle + following candles + yesterday's high.

    `timeframe` is minutes (5 or 15).  On 5-min, the opening candle is the
    09:15–09:20 bar and the confirmation candle (close920) is 09:20.  On
    15-min, the opening candle is the 09:15–09:30 bar and the confirmation
    candle is 09:30.  The EMA (ema200) is computed on closes of the same
    interval so "everything" follows the selected timeframe.

    Returns dict with keys:
      open915, high915, low915, close915, close920 (| None),
      yesterday_high, day_low, near_high_pct (| None)
    or None when Yahoo has no usable data (delisted / no bars today yet).
    """
    sym = str(symbol).strip().upper().replace(".NS", "")
    ticker = f"{sym}.NS"
    now = time.time()
    with _YF_ORB_LOCK:
        hit = _YF_ORB_CACHE.get((sym, timeframe))
        if hit:
            ttl = _YF_ORB_CACHE_TTL if hit[1] is not None else _YF_ORB_FAIL_TTL
            if now - hit[0] < ttl:
                return hit[1]
    try:
        candles = yf.download(
            tickers=ticker,
            period="12d",
            interval=f"{timeframe}m",
            progress=False,
            auto_adjust=False,
            prepost=False,
            threads=False,
        )
    except Exception:
        with _YF_ORB_LOCK:
            _YF_ORB_CACHE[(sym, timeframe)] = (time.time(), None)
        return None
    if candles is None or candles.empty:
        with _YF_ORB_LOCK:
            _YF_ORB_CACHE[(sym, timeframe)] = (time.time(), None)
        return None
    if isinstance(candles.columns, pd.MultiIndex):
        try:
            candles = candles.xs(ticker, axis=1, level=-1)
        except (KeyError, IndexError):
            try:
                candles = candles.xs(ticker, axis=1, level=0)
            except (KeyError, IndexError):
                return None
    for col in ("Open", "High", "Low", "Close"):
        if col not in candles.columns:
            return None

    idx = pd.DatetimeIndex(candles.index)
    if idx.tz is None:
        idx = idx.tz_localize("UTC").tz_convert(IST)
    else:
        idx = idx.tz_convert(IST)
    candles = candles.copy()
    candles.index = idx
    # Anchor date: today when it's a live trading day, otherwise the most
    # recent trading day (weekend / holiday / after-hours → show last
    # working day's data instead of an empty screener).
    anchor_date, _anchor_live = _resolve_reference_date()
    today_rows = candles[candles.index.date == anchor_date]
    if today_rows.empty:
        # Fallback: newest date with a 9:15 bar inside this symbol's history.
        cand_dates = sorted({
            d for d in set(candles.index.date)
            if len(candles[(candles.index.date == d) & (candles.index.hour == 9)
                           & (candles.index.minute >= 15)]) > 0
        }, reverse=True)
        if not cand_dates:
            return None  # no usable bars at all (delisted / junk data)
        anchor_date = cand_dates[0]
        today_rows = candles[candles.index.date == anchor_date]
        if today_rows.empty:
            return None
    today = anchor_date

    # 1st candle = the 09:15 IST 5-min bar (first row at minute >= 15)
    opening = today_rows[(today_rows.index.hour == 9) & (today_rows.index.minute >= 15)]
    if opening.empty:
        return None
    c1 = opening.iloc[0]
    high = float(c1["High"])
    low = float(c1["Low"])
    if not high or not low or low <= 0:
        return None
    open_ = float(c1["Open"])
    close = float(c1["Close"])

    # 2nd+ candles = the next bars after the opening candle.  c2 = the 2nd
    # bar (9:20 for 5-min, 9:30 for 15-min), c3 = 3rd (9:25 / 9:45), c4 = 4th
    # (9:30 / 10:00).  Their closes drive the "3 Candles Inside" toggle (close
    # must sit inside the opening candle's high–low range).  close920 always
    # means "the 2nd candle's close" regardless of timeframe (9:20 on 5-min,
    # 9:30 on 15-min).
    sorted_today = today_rows.sort_index()
    c1_ts = opening.index[0]
    following = sorted_today[sorted_today.index > c1_ts]
    _rows = list(following[["High", "Low", "Close"]].itertuples())
    close920 = float(_rows[0].Close) if _rows else None
    _next = {}
    for _i, _tag in enumerate(("c2", "c3", "c4"), start=0):
        if len(_rows) > _i:
            _r = _rows[_i]
            _next[f"{_tag}_hi"] = float(_r.High)
            _next[f"{_tag}_lo"] = float(_r.Low)
            _next[f"{_tag}_close"] = float(_r.Close)
        else:
            _next[f"{_tag}_hi"] = None
            _next[f"{_tag}_lo"] = None
            _next[f"{_tag}_close"] = None

    # Yesterday's high = max High of the most recent prior trading day's bars
    past = candles[candles.index.date < today]
    yesterday_high = None
    if not past.empty:
        prev_day = max(past.index.date)
        prev_rows = candles[candles.index.date == prev_day]
        if not prev_rows.empty:
            yesterday_high = float(prev_rows["High"].max())

    near_high_pct = (
        abs(close - yesterday_high) / yesterday_high * 100
        if yesterday_high and yesterday_high > 0
        else None
    )

    # 200-period EMA on 5-min closes (Yahoo Finance). Computed over closed
    # bars only (before today) so the value is the actual EMA level at
    # today's open — no lookahead from live bars. Used for the "200 EMA"
    # column and the "Above 200 EMA" toggle filter.
    hist = candles[candles.index.date < today]
    closes = pd.to_numeric(hist["Close"], errors="coerce").dropna()
    ema200 = None
    if len(closes) >= EMA_SPAN:
        ema200 = float(closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])

    # Real per-candle timestamps + OHLC for today's bars (oldest→newest),
    # used by the "Share Low" column so its caption shows the ACTUAL candle
    # times (e.g. "09:40 sharing low with 09:25") instead of fabricated ones.
    _today_candles = []
    for _r in sorted_today.itertuples():
        _t = _r.Index
        if getattr(_t, "tzinfo", None) is None:
            _t = _t.tz_localize("UTC").tz_convert(IST)
        else:
            _t = _t.tz_convert(IST)
        _today_candles.append({
            "t": _t.strftime("%H:%M"),
            "high": float(_r.High),
            "low": float(_r.Low),
        })

    result = {
        "timeframe": timeframe,
        "open915": open_,
        "high915": high,
        "low915": low,
        "close915": close,
        "close920": close920,
        "yesterday_high": yesterday_high,
        "day_low": float(today_rows["Low"].min()),
        "near_high_pct": near_high_pct,
        "ema200": ema200,
        "data_date": str(today),
        "c2_hi": _next.get("c2_hi"), "c2_lo": _next.get("c2_lo"), "c2_close": _next.get("c2_close"),
        "c3_hi": _next.get("c3_hi"), "c3_lo": _next.get("c3_lo"), "c3_close": _next.get("c3_close"),
        "c4_hi": _next.get("c4_hi"), "c4_lo": _next.get("c4_lo"), "c4_close": _next.get("c4_close"),
        "today_candles": _today_candles,
    }
    with _YF_ORB_LOCK:
        _YF_ORB_CACHE[(sym, timeframe)] = (time.time(), result)
    return result


# ── Per-trading-day RAM cache for Yahoo ORB facts ──────────────────
# open915/high915/low915/close920/EMA/yesterday_high are FROZEN once the
# 9:20 IST candle closes and never change for the rest of the day.  Without
# this, every filter toggle (and the 30s auto-refresh) re-downloads the
# ~600-symbol 5-min batch — a 240s+ block per request.  A row is sealed
# into the day cache only when it has a real close920 (i.e. the 9:20 candle
# is complete), so pre-9:20 runs keep re-fetching and upgrade naturally.
_ORB_YAHOO_DAY: dict[str, dict[str, dict | None]] = {}  # date -> {sym: row}
_ORB_YAHOO_DAY_LOCK = threading.Lock()
# Single-flight: the browser fires a full refetch on every toggle AND every
# 30s auto-refresh while candle data is still forming. Without this, N
# concurrent requests each launch their own ~600-symbol batch (240s+ each)
# and the server collapses. Only one fetch round runs at a time; the rest
# block on this lock and then serve from the day cache.
_ORB_YAHOO_FETCH_LOCK = threading.Lock()

# ── DISK PERSISTENCE for the ORB Yahoo day cache ──────────────────
# _ORB_YAHOO_DAY holds the frozen day's facts in RAM only. On a fresh Render
# deploy (new process, empty RAM), the first screener request re-downloads the
# whole 600-stock batch from Yahoo — exactly what times it out.  Persisting the
# day cache to disk means a redeploy reuses the already-computed facts instead
# of re-fetching them.  File is keyed by date so a new trading day naturally
# opens a fresh cache.
import os as _os
from pathlib import Path as _Path

_ORB_YAHOO_CACHE_PATH = _Path(__file__).resolve().parent.parent / "stocks" / "orb_yahoo_day_cache.json"


def _orb_yahoo_load_from_disk() -> dict:
    """Load Yahoo day-cache rows from disk (if present).

    JSON has no tuple keys, so the cache_key ("date", timeframe) is stored
    as a string in the file and converted back to a tuple here.
    """
    try:
        if not _ORB_YAHOO_CACHE_PATH.exists():
            return {}
        with open(_ORB_YAHOO_CACHE_PATH) as _f:
            data = json.load(_f)
        raw_caches = data.get("caches", {})
        out: dict = {}
        for _key, _rows in raw_caches.items():
            # key was stored as "date|timeframe" — reconstruct the tuple.
            if isinstance(_key, str):
                _parts = _key.split("|")
                try:
                    _k = (_parts[0], int(_parts[1]))
                except (IndexError, ValueError):
                    continue
            else:
                _k = _key
            out[_k] = _rows
        return out
    except Exception:
        return {}


def _orb_yahoo_save_to_disk() -> None:
    """Persist the in-memory Yahoo day cache to disk (date-scoped, small)."""
    try:
        _ORB_YAHOO_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Keys are (date_str, timeframe) tuples — JSON can't hold tuple keys,
        # so encode them as "date|timeframe" strings.
        serializable = {}
        for _key, _rows in _ORB_YAHOO_DAY.items():
            if isinstance(_key, tuple) and len(_key) == 2:
                serializable[f"{_key[0]}|{_key[1]}"] = _rows
            else:
                serializable[str(_key)] = _rows
        payload = {
            "last_updated": datetime.datetime.now(IST).isoformat(),
            "caches": serializable,
        }
        with open(_ORB_YAHOO_CACHE_PATH, "w") as _f:
            json.dump(payload, _f, default=str)
    except Exception:
        pass  # never break the request over a cache-write hiccup


def _orb_yahoo_seed_disk_cache() -> None:
    """Seed the RAM cache from disk at import time (called once below)."""
    try:
        disk = _orb_yahoo_load_from_disk()
        if disk:
            with _ORB_YAHOO_DAY_LOCK:
                for _key, _rows in disk.items():
                    if _key not in _ORB_YAHOO_DAY:
                        _ORB_YAHOO_DAY[_key] = _rows
            logger.info(
                f"[yf] Seeded ORB Yahoo day cache from disk: {sum(len(v) for v in disk.values())} rows"
            )
    except Exception:
        pass


def batch_yahoo_orb_data(symbols: list[str], timeframe: int = 5) -> dict:
    """Fetch Yahoo candle data for many symbols in parallel.

    Single-flight + per-IST-day/per-timeframe RAM cache: the opening-candle
    facts (open915/high915/low915/close920/EMA/yesterday_high) are frozen
    once the second candle (9:20 on 5-min, 9:30 on 15-min) closes. Completed
    rows are sealed into the day cache so every later toggle / auto-refresh
    re-filters cached rows instead of re-downloading the universe.
    """
    unique = [str(s).strip().upper() for s in symbols if s]
    if not unique:
        return {}
    # Key the day cache by the *anchor* date (today if live, else the last
    # trading day) AND the timeframe, so 5-min and 15-min rows never collide.
    _anchor, _ = _resolve_reference_date()
    today = _anchor.strftime("%Y-%m-%d")
    cache_key = (today, int(timeframe))

    with _ORB_YAHOO_FETCH_LOCK:
        # Re-check under the single-flight lock so waiters benefit from the
        # round that just finished instead of launching their own.
        with _ORB_YAHOO_DAY_LOCK:
            day_cache = _ORB_YAHOO_DAY.setdefault(cache_key, {})
            results = {s: day_cache[s] for s in unique if s in day_cache}
            missing = [s for s in unique if s not in day_cache]
        if not missing:
            return results

        # ── Hard timeout guard ─────────────────────────────────────────
        # On Render free tier each yfinance download can be slow / rate-limited.
        # If the whole batch would exceed the cap, bail out and return whatever
        # was already fetched (or an empty dict).  Returning partial/unfiltered
        # data keeps the screener responsive instead of timing out the request.
        fresh: dict[str, dict | None] = {}
        if missing:
            import concurrent.futures as _cf
            start_ts = time.time()
            with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
                futures = {
                    pool.submit(fetch_yahoo_orb_data, sym, int(timeframe)): sym
                    for sym in missing
                }
                done_count = 0
                for future in _cf.as_completed(futures):
                    sym = futures[future]
                    try:
                        fresh[sym] = future.result()
                    except Exception:
                        fresh[sym] = None
                    done_count += 1
                    # Safety valve: if we've already burned the whole timeout,
                    # stop waiting on the remaining futures. They'll keep running
                    # in the threadpool and eventually finish, but this request
                    # returns cleanly on time.
                    if time.time() - start_ts > YAHOO_BATCH_TIMEOUT:
                        logger.warning(
                            f"[yf] Yahoo batch timed out after {YAHOO_BATCH_TIMEOUT:.0f}s "
                            f"({done_count}/{len(missing)} done) — returning partial data"
                        )
                        break
        results.update(fresh)
        # Seal only complete rows into the day cache.  None / pre-second-candle
        # rows (close920 missing) are left unsealed so a later round upgrades
        # them the moment the second candle closes — then frozen all day.
        with _ORB_YAHOO_DAY_LOCK:
            day_cache = _ORB_YAHOO_DAY.setdefault(cache_key, {})
            for s, row in fresh.items():
                if row and row.get("close920") is not None:
                    day_cache[s] = row
        # Persist the sealed cache to disk so a fresh Render deploy reuses it
        # instead of re-downloading all ~600 symbols from Yahoo.
        if fresh:
            _orb_yahoo_save_to_disk()
    return results


# Seed the day-cache from disk at module import time so a fresh Render deploy
# doesn't re-download the ~600-stock Yahoo batch before serving the screener.
_orb_yahoo_seed_disk_cache()

def above_200_ema_symbols(symbols: list[str], timeframe: int = 5) -> set[str]:
    """Symbols whose 9:15 opening-candle CLOSE sits ABOVE the 200 EMA.

    Mirrors the Advance ORB "Above 200 EMA" toggle exactly:
      * Compares the 9:15 opening candle's CLOSE (``close915``) against the
        200 EMA computed on PRIOR days' closes (``ema200``) — no lookahead.
      * Keeps a symbol only when close > ema AND close is at most
        ``ABOVE_EMA_MAX_GAP``% above it (4% cap — user rule, never remove).
      * Fail-open: if Yahoo has no data (delisted / rate-limited) or no EMA /
        opening-close, the symbol is KEPT rather than wrongly dropped.

    Returns a set of the base symbols (no exchange prefix) that pass.
    """
    unique = sorted({str(s).strip().upper().replace(".NS", "") for s in symbols if s})
    if not unique:
        return set()
    yahoo = batch_yahoo_orb_data(unique, timeframe=timeframe)
    _missing = [s for s in unique if s not in yahoo]
    fb = compute_200_ema_batch(_missing) if _missing else {}
    ok: set[str] = set()
    for s in unique:
        yd = yahoo.get(s) or {}
        close = yd.get("close915")
        ema = yd.get("ema200")
        if ema is None or float(ema) <= 0:
            ema = fb.get(s)
        if ema is None or float(ema) <= 0:
            ok.add(s)  # can't validate → keep (fail-open)
            continue
        if close is None:
            ok.add(s)  # no opening-close → can't prove below → keep
            continue
        gap_pct = (float(close) - float(ema)) / float(ema) * 100
        if float(close) > float(ema) and gap_pct <= ABOVE_EMA_MAX_GAP:
            ok.add(s)
    return ok


def _calc_qty_for_broker(df, budget, parts):
    """Add a MaxQty column to *df* using whichever broker is currently connected."""
    use_name_close = 'name' in df.columns and 'close' in df.columns
    use_sym_price = (
        any(c.upper() == 'SYMBOL' for c in df.columns)
        and any(c.upper() == 'PRICE' for c in df.columns)
    )
    if not (use_name_close or use_sym_price):
        df["MaxQty"] = 0
        return df

    def _map_qty(symbols, prices):
        dhan_token = _broker_cred("access_token")
        if dhan_token:
            temp = pd.DataFrame({"Symbol": symbols, "Price": prices})
            temp = calculate_max_quantity_column(
                temp, total_capital=budget, num_parts=parts,
                access_token=dhan_token,
            )
            qty_map = dict(zip(temp["Symbol"], temp["MaxQty"]))
            if sum(qty_map.values()) == 0 and angel_is_connected():
                result = angel_calculate_quantities(
                    symbols, prices, total_capital=budget, num_parts=parts,
                )
                result = {s: result.get(s, 0) for s in symbols}
                if sum(result.values()) > 0:
                    return result
            return qty_map
        if angel_is_connected():
            result = angel_calculate_quantities(
                symbols, prices, total_capital=budget, num_parts=parts,
            )
            return {s: result.get(s, 0) for s in symbols}
        return {s: 0 for s in symbols}

    if use_name_close:
        syms = df['name'].astype(str).tolist()
        prcs = pd.to_numeric(df['close'], errors='coerce').tolist()
        qty_map = _map_qty(syms, prcs)
        df['MaxQty'] = df['name'].map(qty_map).fillna(0).astype(int)
    else:
        sc = next(c for c in df.columns if c.upper() == 'SYMBOL')
        pc = next(c for c in df.columns if c.upper() == 'PRICE')
        syms = df[sc].astype(str).tolist()
        prcs = pd.to_numeric(df[pc], errors='coerce').tolist()
        qty_map = _map_qty(syms, prcs)
        df['MaxQty'] = df[sc].map(qty_map).fillna(0).astype(int)
    return df


def _build_ticks_by_symbol():
    """Return dict of {symbol: tick_data} with base-symbol aliases.

    Filters out non-EQ instruments — derivatives (PE/CE), currency pairs
    (USDINR, EURINR, GBPINR, JPYINR) — so only equity segment stocks appear.

    Source preference: Dhan market feed (tick-by-tick) when its WebSocket is
    connected; the Angel One WebSocket fills any symbols Dhan doesn't cover.
    """
    import re as _re
    from broker.angel_ws import get_latest_ticks as angel_ws_ticks
    from broker.dhan_ws import (
        get_latest_ticks as dhan_ws_ticks,
        is_ws_connected as dhan_ws_connected,
    )

    # Reject patterns: PE/CE suffix, currency pairs
    _NON_EQ_RE = _re.compile(r"(PE|CE)$|^(USD|EUR|GBP|JPY)INR", _re.IGNORECASE)

    sources = []
    if dhan_ws_connected():
        dhan = dhan_ws_ticks()
        if dhan:
            sources.append(dhan)
    sources.append(angel_ws_ticks())

    by_symbol = {}
    for ticks in sources:
        for token, data in ticks.items():
            sym = (data.get("symbol") or "").strip()
            if not sym:
                continue
            if _NON_EQ_RE.search(sym):
                continue
            if sym in by_symbol:
                # Dhan is preferred — Angel only fills symbols Dhan doesn't have
                continue
            entry = {
                "ltp": data.get("ltp"),
                "change_pct": data.get("change_pct"),
                "volume": data.get("volume"),
                "high": data.get("high"),
                "low": data.get("low"),
                "open": data.get("open"),
                "timestamp": data.get("timestamp"),
            }
            by_symbol[sym] = entry
            base = sym.split("-")[0]
            if base != sym and not _NON_EQ_RE.search(base):
                by_symbol[base] = entry
    return by_symbol


_SUBSCRIBE_ATTEMPTED: set = set()   # symbols already resolved+added this process


def ws_auto_subscribe(symbols: list[str]):
    """Add table symbols to the Angel One WebSocket watchlist.

    Idempotent per process: symbols already attempted are skipped instantly,
    so calling this on every screener fetch (30s auto-refresh + manual runs)
    stays cheap instead of re-resolving + re-logging the whole table each time.
    """
    from broker.angel_margin_calculator import (
        is_connected as angel_is_connected,
        resolve_symbol_token as _resolve,
    )
    from broker.angel_ws import add_to_watchlist as angel_ws_add
    global _SUBSCRIBE_ATTEMPTED
    if not angel_is_connected():
        return
    todo = [s for s in set(s for s in symbols if s)
            if s not in _SUBSCRIBE_ATTEMPTED]
    if not todo:
        return
    for sym in todo:
        _SUBSCRIBE_ATTEMPTED.add(sym)
        try:
            name, token_str = _resolve(sym.upper(), "NSE")
            if token_str:
                angel_ws_add(name, int(token_str))
        except Exception:
            pass