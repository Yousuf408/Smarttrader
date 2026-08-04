"""
Shared constants and helper functions used by Advance ORB and Big Players strategies.
"""

from zoneinfo import ZoneInfo
import datetime
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

# ── 200-period EMA (auto-buy gate) ──
EMA_SPAN = 200
EMA_LOOKBACK_DAYS = 4

IST = ZoneInfo("Asia/Kolkata")
MAX_TV_STOCKS = 100
YFINANCE_WORKERS = 8

# ── TradingView scanner (Advance ORB universe) ─────────────────────
TV_SCAN_URL = "https://scanner.tradingview.com/india/scan"
TV_SCAN_TTL = 600          # 10 minutes — don't hammer the free endpoint
TV_SCAN_MAX_RESULTS = 1500
_tv_scan_lock = threading.Lock()
_tv_scan_cache: list[dict] = []
_tv_scan_cached_at = 0.0


def fetch_tradingview_stocks(max_results: int = TV_SCAN_MAX_RESULTS) -> list[dict]:
    """NSE universe straight from TradingView (not the local watchlist).

    Screen: type=stock AND exchange=NSE AND
            close 200–4000 INR AND market_cap_basic > 41B INR.
    Returns all matching rows as
        [{name, close, change, gap, volume, relative_volume,
          market_cap_basic, sector, open, high, low}, ...]
    WARNING: open/high/low here are the FULL-DAY bar (the scan's base
    row is the daily snapshot, and the `interval` param is ignored), so
    they must NEVER be used as the 9:15 opening candle.  The True 9:15
    values in Advance ORB come from CandleTracker slot 0 or the authenticated
    TradingView chart feed in get_advance_orb().
    Results are cached for TV_SCAN_TTL seconds.  On network / API
    failure returns the stale cache if any, else an empty list (the
    caller falls back to the WebSocket watchlist path).
    """
    global _tv_scan_cache, _tv_scan_cached_at
    now = time.time()
    with _tv_scan_lock:
        if _tv_scan_cache and (now - _tv_scan_cached_at) < TV_SCAN_TTL:
            return _tv_scan_cache

    payload = {
        "symbols": {"tickers": [], "query": {"types": []}},
        "columns": [
            "name", "description", "close", "change", "gap",
            "volume", "relative_volume_10d_calc", "market_cap_basic",
            "sector",
            "open", "high", "low",
        ],
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "equal", "right": "NSE"},
            {"left": "close", "operation": "greater", "right": PRICE_MIN},
            {"left": "close", "operation": "less", "right": PRICE_MAX},
            {"left": "market_cap_basic", "operation": "greater", "right": MARKET_CAP_MIN},
        ],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
        "range": [0, max_results],
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        ),
    }
    try:
        import requests
        resp = requests.post(TV_SCAN_URL, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        body = resp.json()
    except Exception as e:
        logger.warning("tv-scan: TradingView scan failed: %s", e)
        with _tv_scan_lock:
            return _tv_scan_cache  # stale data beats an empty tab

    rows: list[dict] = []
    for item in body.get("data", []):
        d = item.get("d") or []
        if len(d) < 9:
            continue
        name = str(d[0] or "").strip().upper()
        close = d[2]
        if not name or not isinstance(close, (int, float)) or close <= 0:
            continue
        if not (PRICE_MIN < close <= PRICE_MAX):
            continue
        gap = float(d[4]) if isinstance(d[4], (int, float)) else 0.0
        # Advance ORB uses TradingView's scanner gap value as the source of
        # truth. Exclude both gap-ups and gap-downs at or beyond 2%.
        if abs(gap) >= GAP_THRESHOLD:
            continue
        rows.append({
            "name": name,
            "close": float(close),
            "change": float(d[3]) if isinstance(d[3], (int, float)) else 0.0,
            "gap": gap,
            "volume": float(d[5]) if isinstance(d[5], (int, float)) else 0,
            "relative_volume": float(d[6]) if isinstance(d[6], (int, float)) else 0.0,
            "market_cap_basic": float(d[7]) if isinstance(d[7], (int, float)) else 0,
            "sector": str(d[8]) if d[8] else "N/A",
            # 9:15 IST opening bar OHLC straight from TradingView.
            "open": float(d[9]) if len(d) > 9 and isinstance(d[9], (int, float)) else None,
            "high": float(d[10]) if len(d) > 10 and isinstance(d[10], (int, float)) else None,
            "low": float(d[11]) if len(d) > 11 and isinstance(d[11], (int, float)) else None,
        })
    rows.sort(key=lambda r: -r["market_cap_basic"])

    with _tv_scan_lock:
        _tv_scan_cache = rows
        _tv_scan_cached_at = time.time()
    logger.info("tv-scan: %d NSE stocks (200–4000 INR, mcap > 41B)", len(rows))
    return rows


def has_small_opening_candle(symbol: str) -> bool:
    """Return whether the latest available 9:15 IST five-minute candle is small."""
    ticker = f"{str(symbol).strip().upper()}.NS"
    try:
        candles = yf.download(
            tickers=ticker,
            period="4d",
            interval="5m",
            progress=False,
            auto_adjust=False,
            prepost=False,
            threads=False,
        )
    except Exception:
        return False

    if candles.empty:
        return False

    if isinstance(candles.columns, pd.MultiIndex):
        try:
            candles = candles.xs(ticker, axis=1, level=-1)
        except (KeyError, IndexError):
            try:
                candles = candles.xs(ticker, axis=1, level=0)
            except (KeyError, IndexError):
                return False

    if "High" not in candles or "Low" not in candles:
        return False

    local_index = pd.DatetimeIndex(candles.index)
    if local_index.tz is None:
        local_index = local_index.tz_localize('UTC').tz_convert(IST)
    else:
        local_index = local_index.tz_convert(IST)
    candles = candles.copy()
    candles.index = local_index
    today = pd.Timestamp.now(tz=IST).date()

    opening_today = candles[
        (candles.index.date == today) &
        (candles.index.hour == 9) & (candles.index.minute >= 15)
    ]

    if not opening_today.empty:
        candle = opening_today.iloc[0]
    else:
        opening = candles[
            (candles.index.hour == 9) & (candles.index.minute >= 15)
        ]
        if not opening.empty:
            candle = opening.iloc[-1]
        else:
            return False

    high = pd.to_numeric(candle["High"], errors="coerce")
    low = pd.to_numeric(candle["Low"], errors="coerce")
    if pd.isna(high) or pd.isna(low) or low <= 0:
        return False

    candle_range = (high - low) / low * 100
    return candle_range <= SMALL_CANDLE_THRESHOLD


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
            period="4d",
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


def filter_small_opening_candles(symbols: list[str]) -> set[str]:
    """Return set of symbols whose 9:15 IST candle range <= SMALL_CANDLE_THRESHOLD."""
    if not symbols:
        return set()
    unique = [str(s).strip().upper() for s in symbols if s]
    matches: set[str] = set()
    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(has_small_opening_candle, sym): sym for sym in unique}
        for future in as_completed(futures):
            try:
                if future.result():
                    matches.add(futures[future])
            except Exception:
                continue
    return matches


# ── Yahoo Finance ORB candle data (independent of the WebSocket) ────
# Used by the "Yahoo Filter" toggle on Advance ORB: today's 9:15 + 9:20
# candles and yesterday's high are fetched straight from Yahoo Finance.
# Cached briefly so the frontend's auto-refresh doesn't hammer Yahoo.
_YF_ORB_CACHE: dict[str, tuple[float, dict | None]] = {}
_YF_ORB_CACHE_TTL = 300.0   # 5-min cache for successful fetches
_YF_ORB_FAIL_TTL = 60.0     # failed fetches retried after 1 min
_YF_ORB_LOCK = threading.Lock()


def fetch_yahoo_orb_data(symbol: str) -> dict | None:
    """Fetch today's 9:15/9:20 5-min candles + yesterday's high from Yahoo.

    Returns dict with keys:
      open915, high915, low915, close915, close920 (| None),
      yesterday_high, day_low, near_high_pct (| None)
    or None when Yahoo has no usable data (delisted / no bars today yet).
    """
    sym = str(symbol).strip().upper().replace(".NS", "")
    ticker = f"{sym}.NS"
    now = time.time()
    with _YF_ORB_LOCK:
        hit = _YF_ORB_CACHE.get(sym)
        if hit:
            ttl = _YF_ORB_CACHE_TTL if hit[1] is not None else _YF_ORB_FAIL_TTL
            if now - hit[0] < ttl:
                return hit[1]
    try:
        candles = yf.download(
            tickers=ticker,
            period="4d",
            interval="5m",
            progress=False,
            auto_adjust=False,
            prepost=False,
            threads=False,
        )
    except Exception:
        with _YF_ORB_LOCK:
            _YF_ORB_CACHE[sym] = (time.time(), None)
        return None
    if candles is None or candles.empty:
        with _YF_ORB_LOCK:
            _YF_ORB_CACHE[sym] = (time.time(), None)
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
    today = pd.Timestamp.now(tz=IST).date()

    today_rows = candles[candles.index.date == today]
    if today_rows.empty:
        return None  # no Yahoo bars for today yet (pre-market / holiday)

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

    # 2nd candle = the 09:20 IST bar (first row at minute >= 20)
    c2_rows = today_rows[(today_rows.index.hour == 9) & (today_rows.index.minute >= 20)]
    close920 = float(c2_rows.iloc[0]["Close"]) if not c2_rows.empty else None

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
    result = {
        "open915": open_,
        "high915": high,
        "low915": low,
        "close915": close,
        "close920": close920,
        "yesterday_high": yesterday_high,
        "day_low": float(today_rows["Low"].min()),
        "near_high_pct": near_high_pct,
    }
    with _YF_ORB_LOCK:
        _YF_ORB_CACHE[sym] = (time.time(), result)
    return result


def batch_yahoo_orb_data(symbols: list[str]) -> dict:
    """Fetch Yahoo candle data for many symbols in parallel."""
    unique = [str(s).strip().upper() for s in symbols if s]
    results: dict[str, dict | None] = {}
    if not unique:
        return results
    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(fetch_yahoo_orb_data, sym): sym for sym in unique}
        for future in as_completed(futures):
            sym = futures[future]
            try:
                results[sym] = future.result()
            except Exception:
                results[sym] = None
    return results


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


def ws_auto_subscribe(symbols: list[str]):
    """Add symbols to the Angel One WebSocket watchlist."""
    from broker.angel_margin_calculator import (
        is_connected as angel_is_connected,
        resolve_symbol_token as _resolve,
    )
    from broker.angel_ws import add_to_watchlist as angel_ws_add
    if not angel_is_connected():
        return
    for sym in set(s for s in symbols if s):
        try:
            name, token_str = _resolve(sym.upper(), "NSE")
            if token_str:
                angel_ws_add(name, int(token_str))
        except Exception:
            pass
