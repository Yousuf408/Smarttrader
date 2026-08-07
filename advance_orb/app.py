from pathlib import Path

import asyncio
import time
import re, requests
import pyotp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
import hashlib
from tradingview_screener import Query, col
from tradingview_screener.query import HEADERS as TV_HEADERS
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from broker.quantity_calculator import (
    calculate_max_quantity_column,
    set_dhan_credentials,
    set_dhan_access_token,
    clear_dhan_credentials,
    _cred as _broker_cred,
    DHAN_PROXIES,
    renew_dhan_access_token as _dhan_renew,
    DHAN_TOKEN_TTL_SECONDS,
    DHAN_AUTO_RENEW_LEAD_SECONDS,
)
from broker.dhan_orders import place_dhan_order
from broker.angel_orders import place_angel_order, modify_angel_order, register_sl_order
from broker.quantity_calculator import DHAN_ACCESS_TOKEN as dhan_token
from broker.angel_margin_calculator import (
    set_credentials as set_angel_credentials,
    authenticate as angel_authenticate,
    is_connected as angel_is_connected,
    disconnect as angel_disconnect,
    get_access_token as angel_get_access_token,
    get_feed_token as angel_get_feed_token,
    calculate_quantities as angel_calculate_quantities,
    _CREDS as _angel_creds,
    ANGEL_PROXIES,
    is_stock_tradable,
)
from broker.angel_ws import (
    start_websocket as angel_ws_start,
    stop_websocket as angel_ws_stop,
    get_latest_ticks as angel_ws_ticks,
    set_watchlist as angel_ws_watchlist,
    is_ws_connected as angel_ws_connected,
    add_to_watchlist as angel_ws_add,
    get_subscription_status as angel_ws_status,
)
try:
    from broker.dhan_ws import (
        start_dhan_ws as dhan_feed_start,
        is_ws_connected as dhan_feed_connected,
    )
except ImportError:
    dhan_feed_start = None
    dhan_feed_connected = lambda: False
from advance_orb.supabase_db import save_top5_strategy, ensure_table
from advance_orb.auth_routes import router as auth_router
from server.candle_tracker import candle_tracker
from advance_orb.tv_chart_candles import batch_tv_opening_candles
from advance_orb.common import batch_yahoo_orb_data

# Ensure the strategy_trades table exists (run once at startup)
ensure_table()


# =================================================================
# DAILY-AUTO-RENEW BACKGROUND TASK
# =================================================================
# /api/broker/connect mints a fresh JWT once via TOTP; Dhan's own
# /RenewToken endpoint lets the backend swap a fresh JWT into the
# in-memory store without bothering the user. This loop fires
# ~1 h before the 24 h TTL expires and does exactly that, so
# screener MaxQty stays live across the daily rollover.
# The renew call goes through asyncio.to_thread because requests
# is blocking, and we don't want the event loop to stall on the
# round-trip while /api/strategies/* traffic is incoming.
# =================================================================
async def _dhan_auto_renew_loop():
    while True:
        sleep_for = 60.0  # default poll cadence when not connected
        try:
            issued = _broker_cred("token_issued_at")
            tok    = _broker_cred("access_token")
            if issued and tok:
                age = time.time() - float(issued)
                # Renew when age crosses (TTL - lead) ≈ 23 h elapsed.
                renew_at_age = (
                    DHAN_TOKEN_TTL_SECONDS - DHAN_AUTO_RENEW_LEAD_SECONDS
                )
                if age >= renew_at_age:
                    print(
                        f"[broker] auto-renewing access token "
                        f"(age={age/3600:.2f}h)"
                    )
                    res = await asyncio.to_thread(_dhan_renew)
                    print(
                        f"[broker] auto-renew result: "
                        f"ok={res.get('ok')} "
                        f"status={res.get('status_code')} "
                        f"detail={(res.get('detail') or '')[:120]}"
                    )
                # Re-read after the renew (token_issued_at is preserved
                # on renewals, so age is the same; but count_renews_at
                # is now bumped — future ticks know we did one shot).
                age = time.time() - float(issued)
                remaining = max(0.0, renew_at_age - age)
                sleep_for = min(
                    max(30.0, remaining + 5.0),
                    15 * 60.0,  # never sleep more than 15 min
                )
        except Exception as e:
            print(f"[broker] auto-renew loop tick error: {e!r}")
        await asyncio.sleep(sleep_for)


# =================================================================
# ANGEL ONE AUTO-RENEW LOOP — same pattern as Dhan, but using
# authenticate() (TOTP-based re-login) since Angel has no /RenewToken.
# Checks every 5 min; re-auths when token age > 12 h.
# =================================================================
_ANGEL_TOKEN_TTL_SECONDS = 24 * 3600       # 24 h (typical Angel WS expiry)
_ANGEL_AUTO_RENEW_LEAD_SECONDS = 12 * 3600  # re-auth after 12 h

async def _angel_auto_renew_loop():
    while True:
        sleep_for = 300.0  # default: check every 5 min
        try:
            issued_str = _angel_creds.get("token_issued_at", "0")
            tok = _angel_creds.get("access_token", "")
            if issued_str and tok:
                try:
                    issued = float(issued_str) if issued_str else 0
                except ValueError:
                    issued = 0
                age = time.time() - issued
                renew_at_age = (
                    _ANGEL_TOKEN_TTL_SECONDS - _ANGEL_AUTO_RENEW_LEAD_SECONDS
                )
                if age >= renew_at_age:
                    print(
                        f"[broker-angel] auto-renewing access token "
                        f"(age={age/3600:.2f}h)"
                    )
                    res = await asyncio.to_thread(angel_authenticate)
                    print(
                        f"[broker-angel] auto-renew result: "
                        f"ok={res.get('ok')} "
                        f"{'error: '+str(res.get('error',''))[:120] if not res.get('ok') else ''}"
                    )
                age = time.time() - float(issued or "0")
                remaining = max(0.0, renew_at_age - age)
                sleep_for = min(
                    max(30.0, remaining + 5.0),
                    15 * 60.0,  # never sleep more than 15 min
                )
        except Exception as e:
            print(f"[broker-angel] auto-renew loop tick error: {e!r}")
        await asyncio.sleep(sleep_for)


@asynccontextmanager
async def lifespan(_app):
    task_dhan = asyncio.create_task(_dhan_auto_renew_loop())
    task_angel = asyncio.create_task(_angel_auto_renew_loop())
    print("[broker] daily auto-renew loop started (Dhan + Angel)")
    try:
        yield
    finally:
        task_dhan.cancel()
        task_angel.cancel()
        try:
            await task_dhan
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await task_angel
        except (asyncio.CancelledError, Exception):
            pass


app = FastAPI(
    title="TradeAlgo Pro - Advance ORB",
    description="Fetches NSE stocks with price 200-3000, gap < 2%, market cap > 41B",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS - Allow frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auth router — signup, signin, me, logout via Supabase Auth
app.include_router(auth_router)

# No-cache for every response — HTML, JS, CSS, JSON. After the
# auth-strip the user's browser held stale copies of (a) the deleted
# /login page, (b) the cached index.html with the inline auth gate,
# and (c) static assets carrying the pre-toast-fix styles. Forcing
# the browser to revalidate every request guarantees the version on
# disk is what's running. Trade-off: more bandwidth per reload —
# acceptable for a dashboard app of this size.
@app.middleware("http")
async def _no_cache_all(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response

from advance_orb.common import (
    PRICE_MIN, PRICE_MAX, GAP_THRESHOLD, MARKET_CAP_MIN,
    SMALL_CANDLE_THRESHOLD, ABOVE_EMA_MAX_GAP,
    IST, MAX_TV_STOCKS, YFINANCE_WORKERS,
    compute_200_ema, compute_200_ema_batch,
    fetch_tradingview_stocks,
    _detect_trading_date, _resolve_reference_date, _calc_qty_for_broker,
    _build_ticks_by_symbol, ws_auto_subscribe,
)
from bigplayers.routes import router as bigplayers_router
app.include_router(bigplayers_router)

ADVANCE_ORB_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "GAP%",
    "Volume",
    "RELVOL",
    "Sector",
    "200 EMA",
    "1st High",
    "1st Low",
    "1st Range%",
    "Inside 9:15",
    "MaxQty",
]

@app.get("/api")
def root():
    return {
        "status": "ok",
        "message": "Advance ORB + Big Players Strategy API",
        "conditions": {
            "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
            "gap": f"< {GAP_THRESHOLD}%",
            "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
            "exchange": "NSE"
        },
        "strategies": ["advanceorb", "bigplayers"]
    }

# (broker quantity helper lives in advance_orb.common)


@app.get("/api/strategies/advanceorb")
def get_advance_orb(budget: int = 100000, parts: int = 4, near_high: bool = True,
                    above_ema: bool = False, inside915: bool = False,
                    inside3: bool = False, calc_qty: bool = False,
                    timeframe: int = 5):
    """
    Fetch the NSE universe straight from TradingView:
    1. Price: 200 to 4000 INR
    2. Market Cap: > 41B INR
    3. Exchange: NSE

    All matching stocks are shown (candle/inside-9:15 conditions are
    re-applied in later design steps).

    Query params:
      budget:    total capital in INR (default 100000)
      parts:     number of equal parts to split budget into (default 4)
      timeframe: candle timeframe in minutes — 5 (default) or 15.  ALL candle
                 logic follows the selected timeframe: the opening candle, the
                 inside-/3-candle checks, and the 200 EMA are computed from
                 bars of this size.
    """
    if budget <= 0:
        raise HTTPException(status_code=400, detail="budget must be > 0")
    if parts < 1 or parts > 20:
        raise HTTPException(status_code=400, detail="parts must be between 1 and 20")
    if int(timeframe) not in (5, 15):
        raise HTTPException(status_code=400, detail="timeframe must be 5 or 15")
    timeframe = int(timeframe)
    try:
        # ─── Step 1: Universe — TradingView scan (primary) ──────────────
        # Fetch ALL NSE stocks straight from TradingView:
        #   type=stock, exchange=NSE, close 200–4000 INR, mcap > 41B INR.
        # Every returned stock is shown — refinement conditions come in
        # later steps and via the toggles.
        # Falls back to the watchlist + WebSocket path if the scanner
        # is unreachable so the tab never goes empty.
        tv_rows = fetch_tradingview_stocks()
        cache_data = candle_tracker._cache if hasattr(candle_tracker, '_cache') else {}
        if tv_rows:
            raw_rows = [
                {
                    "name": t["name"],
                    "close": t["close"],
                    "change": t["change"],
                    "gap": t["gap"],
                    "volume": t["volume"],
                    "relative_volume": t["relative_volume"],
                    "market_cap_basic": t["market_cap_basic"],
                    "sector": t["sector"],
                    # day-open isn't available from the TV scan; open915
                    # falls back to CandleTracker candles when present.
                    "tick_open": None,
                }
                for t in tv_rows
            ]
        else:
            # ── Fallback: 727-stock watchlist + WebSocket ticks ──
            ws_ticks = angel_ws_ticks()

            raw_rows: list[dict] = []
            for sym in candle_tracker.token_by_symbol:
                tok = candle_tracker.token_by_symbol[sym]
                ws = ws_ticks.get(str(tok), {})
                cached = cache_data.get(sym, {})

                # Validate WS symbol — Angel One returns currency derivative
                # data for tokens shared across NSE equity and CDS segments.
                # If the symbol doesn't match {SYM}-EQ, fall back to
                # yesterday's close.
                ws_symbol = (ws.get("symbol") or "").upper()
                expected_symbol = f"{sym}-EQ"
                ltp = ws.get("ltp")
                change_pct = ws.get("change_pct", 0)

                if not ws_symbol or ws_symbol != expected_symbol.upper():
                    # Bad WS data — use yesterday's close as fallback price
                    yc = cached.get("yesterday_close")
                    if yc and float(yc) > 0 and PRICE_MIN < float(yc) <= PRICE_MAX:
                        ltp = float(yc)
                        change_pct = 0
                        gap_pct = 0
                    else:
                        continue
                else:
                    if ltp is None or float(ltp) <= 0:
                        continue
                    yc = cached.get("yesterday_close")
                    gap_pct = ((float(ltp) - float(yc)) / float(yc) * 100) if yc and float(yc) > 0 else None
                    if gap_pct is None or abs(gap_pct) >= GAP_THRESHOLD:
                        continue
                    if not (PRICE_MIN < float(ltp) <= PRICE_MAX):
                        continue

                raw_rows.append({
                    "name": sym,
                    "close": float(ltp),
                    "change": change_pct,
                    "gap": gap_pct,
                    "volume": ws.get("volume", 0),
                    "relative_volume": 0.0,
                    "market_cap_basic": 0,
                    "sector": "N/A",
                    # day-open from Angel One tick (= NSE 9:15 open price).
                    # Used as open915 fallback when CandleTracker has no
                    # completed slot-0 data (e.g. after a server restart
                    # before the first 5-min boundary fires).
                    "tick_open": ws.get("open"),
                })

        df = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()
        count = len(df)

        if count == 0:
            return {
                "strategy": "advanceorb",
                "name": "Advance ORB",
                "count": 0,
                "data": [],
                "columns": ADVANCE_ORB_COLUMNS,
                "message": "No stocks found matching the conditions"
            }

        candidate_symbols = df['name'].dropna().astype(str).tolist()

        # "Filter Near High" toggle: today's Yahoo open must be within ±2% of
        # the previous trading day's high. This is deliberately separate from
        # the 9:15 candle columns below, which remain TradingView-only and
        # pending until that separate requirement is enabled. When the toggle
        # is OFF, the full TradingView universe (~600) is shown instead.
        if near_high or above_ema or inside915 or inside3:
            yahoo_open_high = batch_yahoo_orb_data(candidate_symbols, timeframe=timeframe)
        else:
            yahoo_open_high = None

        if near_high:
            yahoo_near_high_symbols = set()
            for _s, _yd in (yahoo_open_high or {}).items():
                if not _yd:
                    continue
                _open = _yd.get("open915")
                _prev_high = _yd.get("yesterday_high")
                if _open is None or _prev_high is None or float(_prev_high) <= 0:
                    continue
                if 0.98 * float(_prev_high) <= float(_open) <= 1.02 * float(_prev_high):
                    yahoo_near_high_symbols.add(_s)
            df = df[df["name"].isin(yahoo_near_high_symbols)].copy()
            candidate_symbols = df["name"].dropna().astype(str).tolist()

        # "Above 200 EMA" toggle: the opening candle's CLOSE must be ABOVE the
        # 200 EMA and at most ABOVE_EMA_MAX_GAP% above it (opening-candle
        # close, not open, not the 2nd candle) — confirmed with user.
        #
        # IMPORTANT (regression): the filter must NOT silently drop a stock
        # just because Yahoo didn't return candle data for it. Yahoo yfinance
        # frequently rate-limits ("Invalid Crumb" / 401), so a symbol can be
        # absent from `yahoo_open_high` while it is clearly above EMA. The old
        # code iterated only over `yahoo_open_high` keys, so a Yahoo-side miss
        # removed a healthy above-EMA row — exactly the "stock above 200 EMA
        # gets removed" bug. Fix: evaluate every row; if the EMA is missing,
        # fall back to compute_200_ema_batch (CandleTracker, same value shown
        # in the 200 EMA column); if we still can't determine it, KEEP the row
        # (fail-open) rather than wrongly dropping it.
        if above_ema:
            above_ema_symbols = set()
            _yd_all = yahoo_open_high or {}
            _missing = [s for s in df["name"].tolist() if s not in _yd_all]
            _fb_ema = compute_200_ema_batch(_missing) if _missing else {}
            for _s in df["name"].tolist():
                _yd = _yd_all.get(_s)
                _close = _yd.get("close915") if _yd else None
                _ema = _yd.get("ema200") if _yd else None
                if _ema is None or float(_ema) <= 0:
                    _ema = _fb_ema.get(_s)  # yahoo missed → CandleTracker EMA
                if _ema is None or float(_ema) <= 0:
                    above_ema_symbols.add(_s)  # can't validate → keep (fail-open)
                    continue
                if _close is None:
                    above_ema_symbols.add(_s)  # no opening-close → can't prove below → keep
                    continue
                _gap_pct = (float(_close) - float(_ema)) / float(_ema) * 100
                if float(_close) > float(_ema) and _gap_pct <= ABOVE_EMA_MAX_GAP:
                    above_ema_symbols.add(_s)
            df = df[df["name"].isin(above_ema_symbols)].copy()
            candidate_symbols = df["name"].dropna().astype(str).tolist()

        # "3 Candles Inside 9:15" toggle: the CLOSE of the 9:20, 9:25 and
        # 9:30 candles must ALL sit inside the 9:15 candle's high–low range
        # (Yahoo 5-min). Full high/low of those candles is NOT required.
        if inside3:
            inside3_symbols = set()
            for _s, _yd in (yahoo_open_high or {}).items():
                if not _yd:
                    continue
                _hi = _yd.get("high915")
                _lo = _yd.get("low915")
                if _hi is None or _lo is None or float(_hi) <= 0:
                    continue
                _hi, _lo = float(_hi), float(_lo)
                _ok = True
                for _k in ("c2", "c3", "c4"):
                    _kc = _yd.get(f"{_k}_close")
                    if _kc is None:
                        _ok = False
                        break
                    if not (_lo <= float(_kc) <= _hi):
                        _ok = False
                        break
                if _ok:
                    inside3_symbols.add(_s)
            df = df[df["name"].isin(inside3_symbols)].copy()
            candidate_symbols = df["name"].dropna().astype(str).tolist()

        # Open-candle batch: pull each symbol's 9:15 IST 5-min candle in
        # parallel. Returns (is_small, high915, open915, low915,
        # close915, range_pct) per symbol.
        #   * is_small   — used by the small-open-candle gate below
        #   * high915    — read by the auto-buy band-filter on the frontend
        # (open_val is returned for future use but no longer consumed here.)
        # TradingView authenticated chart feed is the sole source for the
        # Advance ORB opening candle. Angel/CandleTracker and Yahoo are not
        # used for high915, low915, open915, or close915.
        # 9:15 candle values are intentionally deferred for now. The current
        # screener only applies the Yahoo open-vs-previous-high universe
        # filter above; chart-feed candle backfill will be enabled separately.
        opening_candle_map = {
            _s: (False, None, None, None, None, None, None, None, None, None)
            for _s in candidate_symbols
        }

        # Only authenticated TradingView chart candles count as ORB candle
        # data. Angel/CandleTracker and Yahoo are deliberately excluded.
        has_candle_data = any(
            isinstance(t, tuple) and t and t[2] for t in opening_candle_map.values()
        )

        # "open915" = today's OPEN price (= the first 5-min candle's Open).
        # Used for the 200-EMA distance check instead of the live close,
        # so a stock that opens within the band keeps its row even when
        # the live price subsequently moves beyond 3% of EMA.
        # Mirror: assign df['high915'] = today's 9:15 IST candle HIGH
        # (= first 5-min candle's High). Read by the JS band-filter
        # in autoBuyAllStocks — NOT part of any screener-side filter.
        df['high915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None))[1]
        )
        df['low915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None))[3]
        )
        df['close915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None))[4]
        )
        if has_candle_data:
            df['open915'] = df['name'].map(
                lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None))[2]
            )
        else:
            # No authenticated TradingView candle yet: remain explicitly
            # empty rather than substituting a broker tick/day-open value.
            df['open915'] = None
        df['candle_range_pct'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None))[5]
        )
        if has_candle_data:
            df['yesterday_high'] = df['name'].map(
                lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None, None, None))[7]
            )
        else:
            # No slot-0 candle data yet — read yesterday_high directly from
            # the strategy cache (pre-populated from yfinance at startup,
            # independent of slot-0 completion).
            df['yesterday_high'] = df['name'].map(
                lambda s: cache_data.get(s, {}).get("yesterday_high")
            )
        df['close920'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None, None, None))[8]
        )
        df['inside_915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None, None, None))[9]
        )
        # "Inside 9:15" filter (Yahoo 5-min data): the 2nd candle (9:20)
        # must close INSIDE the 1st candle's (9:15) high–low range. The
        # TradingView chart-feed candle is still the future source for the
        # 9:15/9:20 columns, but until it's available we already fetch the
        # Yahoo 5-min batch above (open915/high915/low915/close920), so
        # derive inside_915/close920 from it to keep the toggle working.
        if yahoo_open_high:
            _y_close920: dict = {}
            _y_inside: dict = {}
            _y_hi: dict = {}
            _y_lo: dict = {}
            for _s, _yd in yahoo_open_high.items():
                if not _yd:
                    continue
                _lo = _yd.get("low915")
                _hi = _yd.get("high915")
                _c2 = _yd.get("close920")
                _y_hi[_s] = _hi
                _y_lo[_s] = _lo
                if _lo is None or _hi is None or _c2 is None or float(_hi) <= 0:
                    _y_inside[_s] = None
                else:
                    _y_inside[_s] = bool(float(_lo) <= float(_c2) <= float(_hi))
                _y_close920[_s] = _c2
            df['close920'] = df['name'].map(lambda s: _y_close920.get(s))
            df['inside_915'] = df['name'].map(lambda s: _y_inside.get(s))
            # 1st High / 1st Low columns: the TradingView chart-feed candle
            # is still the future source, but until it's available fill these
            # from the Yahoo 5-min 9:15 candle we already fetch. (User asked
            # to display the values we already have instead of empty cells.)
            df['high915'] = df['name'].map(
                lambda s: _y_hi.get(s)
                if _y_hi.get(s) is not None
                else opening_candle_map.get(s, (False, None, None))[1]
            )
            df['low915'] = df['name'].map(
                lambda s: _y_lo.get(s)
                if _y_lo.get(s) is not None
                else opening_candle_map.get(s, (False, None, None, None))[3]
            )
        # NOTE: the TradingView scan's open/high/low are the FULL-DAY bar,
        # not the 9:15 candle — they were once used to override high915/
        # low915 and produced nonsense ranges (e.g. a stock's whole-day
        # range shown as its "9:15 range").  The TV day bar is NEVER used
        # for the 9:15 values; they come only from the authenticated
        # TradingView 5-min chart feed above.
        # 200 EMA column: prefer the Yahoo Finance 5-min EMA (ema200 from
        # the batch above) so the "200 EMA" column always shows the Yahoo
        # 5-min value the filter is built on; fall back to CandleTracker
        # for symbols the batch missed. Missing EMA yields None in the row;
        # auto-buy treats None as 'skip the candidate' (never commit on
        # unvalidated data).
        ema_map: dict = {}
        if yahoo_open_high:
            for _s in candidate_symbols:
                _yd = yahoo_open_high.get(_s)
                if _yd and _yd.get("ema200") is not None:
                    ema_map[_s] = float(_yd["ema200"])
        _missing_ema = [s for s in candidate_symbols if s not in ema_map]
        if _missing_ema:
            _fb = compute_200_ema_batch(_missing_ema)
            ema_map.update({s: v for s, v in _fb.items() if v is not None})
        df['ema'] = df['name'].map(ema_map)

        # Calculate Max Quantities via Dhan (see broker/quantity_calculator.py).
        # quantity_calculator expects Symbol/Price cols; df has name/close.
        # Token from broker runtime store (populated via the broker
        # popup / /api/broker/connect). Empty string falls through
        # so quantity_calculator.requests call hits the missing-token
        # code path and the screener surfaces a clear "no broker"
        # error to the UI instead of a silent bare 500.
        # MaxQty is opt-in via the Calc MaxQty toggle: off = skip the
        # broker margin round-trip for a fast load (column reads 0).
        if calc_qty:
            _calc_qty_for_broker(df, budget, parts)
        else:
            df["MaxQty"] = 0

        result = []
        for _, row in df.iterrows():
            symbol = row['name']

            # Normal mode: open must be within ±2% of yesterday's high
            # (TV daily high).  Only evaluated when both values exist —
            # the TV candle completes ~09:20 — otherwise keep pending.
            _open = row.get("open915")
            _yh = row.get("yesterday_high")
            if pd.notna(_open) and pd.notna(_yh) and float(_yh) > 0:
                _lo_b = 0.98 * float(_yh)
                _hi_b = 1.02 * float(_yh)
                if not (_lo_b <= float(_open) <= _hi_b):
                    continue

            # Format volume
            vol = row.get('volume', 0)
            if vol >= 1_000_000:
                volume_str = f"{vol/1_000_000:.1f}M"
            elif vol >= 1_000:
                volume_str = f"{vol/1_000:.1f}K"
            else:
                volume_str = str(vol)

            # Format relative volume
            relvol = row.get('relative_volume', 0)
            relvol_str = f"{relvol:.2f}x" if pd.notna(relvol) else "0x"

            close_price = float(pd.to_numeric(row['close'], errors='coerce'))
            entry = {
                "Symbol": symbol,
                "Price": round(close_price, 2),
                "CHG%": round(row['change'], 2),
                "GAP%": round(row['gap'], 2),
                "Volume": volume_str,
                "RELVOL": relvol_str,
                "Sector": row.get('sector', 'Unknown'),
                "ema": (
                    round(float(row["ema"]), 2)
                    if pd.notna(row.get("ema"))
                    else None
                ),
                "open915": (
                    round(float(row["open915"]), 2)
                    if pd.notna(row.get("open915"))
                    else None
                ),
                "yesterday_high": (
                    round(float(row["yesterday_high"]), 2)
                    if pd.notna(row.get("yesterday_high"))
                    else None
                ),
                "MaxQty": int(row.get("MaxQty", 0)),
            }

            # Candle detail columns (always included)
            entry["high915"] = (
                round(float(row["high915"]), 2)
                if pd.notna(row.get("high915"))
                else None
            )
            entry["low915"] = (
                round(float(row["low915"]), 2)
                if pd.notna(row.get("low915"))
                else None
            )
            entry["close915"] = (
                round(float(row["close915"]), 2)
                if pd.notna(row.get("close915"))
                else None
            )
            entry["candle_range_pct"] = (
                round(float(row["candle_range_pct"]), 4)
                if pd.notna(row.get("candle_range_pct"))
                else None
            )
            entry["close920"] = (
                round(float(row["close920"]), 2)
                if pd.notna(row.get("close920"))
                else None
            )
            entry["inside_915"] = (
                bool(row["inside_915"])
                if "inside_915" in row.index and pd.notna(row.get("inside_915"))
                else None
            )

            result.append(entry)

        # Sort by CHG% descending (highest gainers first)
        result.sort(key=lambda x: x['CHG%'], reverse=True)

        # Live tick overlay: subscribe exactly the stocks shown in this ORB
        # table to the broker WebSocket (Angel One) so the frontend's SSE
        # stream patches tick-by-tick live prices for them.  Subscribed from
        # the table itself, never from the watchlist.
        try:
            ws_auto_subscribe([str(e["Symbol"]) for e in result])
        except Exception:
            pass  # never break the screener over a subscription hiccup

        out_columns = ADVANCE_ORB_COLUMNS
        conditions = {
            "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
            "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
            "exchange": "NSE",
            "gap": f"TradingView absolute gap < {GAP_THRESHOLD}%",
            "open_near_prev_high": (
                "ON: Yahoo today's open within ±2% of previous day's high"
                if near_high
                else "OFF: full TradingView universe (no near-high filter)"
            ),
            "above_ema_200": (
                f"ON: Yahoo 9:15 candle close above 200 EMA, gap ≤ {ABOVE_EMA_MAX_GAP}%"
                if above_ema
                else "OFF"
            ),
            "inside_915": (
                "ON: 9:20 close inside 9:15 range (Yahoo 5-min)"
                if inside915
                else "OFF"
            ),
            "inside_3_candles": (
                "ON: 9:20/9:25/9:30 candle closes inside 9:15 range (Yahoo 5-min)"
                if inside3
                else "OFF"
            ),
        }
        conditions["universe"] = (
            "TradingView · all NSE stocks · 200–4000 INR · mcap > 41B"
        )

        # Save top 5 to Supabase for historical tracking
        try:
            save_top5_strategy("advanceorb", result[:5])
        except Exception:
            pass  # never break the screener over a DB hiccup

        _ref_date, _is_live = _resolve_reference_date()
        return {
            "strategy": "advanceorb",
            "name": "Advance ORB",
            "count": len(result),
            "data": result,
            "columns": out_columns,
            "conditions": conditions,
            "candle_data_available": has_candle_data,
            "market_closed": not _is_live,
            "reference_date": str(_ref_date),
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/strategies/advanceorb/qty")
def recompute_advanceorb_qty(payload: dict):
    """Lightweight MaxQty recompute for the Advance ORB screener.

    Body: {budget:int, parts:int, symbols:[{Symbol,Price},...]}
    Returns: {data:[{Symbol,MaxQty},...]}

    Skips TradingView scan + yfinance EMA + 9:15 candle pulls so
    budget/parts steppers refresh MaxQty instantly. The caller
    must already hold a screener snapshot from the heavy
    /api/strategies/advanceorb endpoint (Refresh click / strategy
    switch / first load).
    """
    budget  = payload.get("budget")
    parts   = payload.get("parts")
    symbols = payload.get("symbols") or []
    if not isinstance(budget, int) or budget <= 0:
        raise HTTPException(status_code=400, detail="budget must be a positive integer")
    if not isinstance(parts, int) or parts <= 0:
        raise HTTPException(status_code=400, detail="parts must be a positive integer")
    if not isinstance(symbols, list) or not symbols:
        return {"data": []}
    rows = []
    for entry in symbols:
        if not isinstance(entry, dict):
            continue
        sym = entry.get("Symbol") or entry.get("symbol")
        price = entry.get("Price") or entry.get("price")
        if not sym or price is None:
            continue
        try:
            price = float(price)
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        rows.append({"Symbol": sym, "Price": price})
    if not rows:
        return {"data": []}
    df_in = pd.DataFrame(rows)
    _calc_qty_for_broker(df_in, budget, parts)
    out = []
    for sym, q in zip(df_in["Symbol"], df_in["MaxQty"]):
        out.append({
            "Symbol": sym,
            "MaxQty": int(q) if pd.notna(q) else 0,
        })
    return {"data": out}


# ================================================================
# ORDER ROUTING
# ================================================================

def _order_broker():
    """Return the order-placement function for the currently-connected broker.

    Returns:
        (callable, str) — (order_fn, broker_name) where order_fn has the
        same signature as place_dhan_order / place_angel_order.
    Raises:
        HTTPException(400) if no broker is connected.
    """
    if angel_is_connected():
        # Angel One's place_angel_order doesn't accept amo_time (it has
        # after_market_order as a bool).  Strip that keyword before calling.
        def _angel_order(**kw):
            kw.pop("amo_time", None)
            return place_angel_order(**kw)
        return _angel_order, "angel"
    if dhan_token:
        return place_dhan_order, "dhan"
    raise HTTPException(
        status_code=400,
        detail="No broker connected. Connect to Angel One or configure Dhan first.",
    )


# ================================================================
# ORDER ENDPOINTS
# ================================================================

@app.post("/api/orders/place")
def place_order_endpoint(payload: dict):
    """Single-order placement for the manual Place-Order button.

    Body: {
      "symbol": "...", "quantity": int,
      "transactionType": "BUY"|"SELL" (default BUY),
      "productType": "INTRADAY"|"CNC" (default INTRADAY),
      "orderType": "MARKET"|"LIMIT"|"SL"|"SL_M"|"SL_L" (default MARKET),
      "price": float (required for LIMIT/SL_L, default 0),
      "triggerPrice": float (required for SL/SL_M/SL_L, default 0),
      "afterMarketOrder": bool (default False),
      "amoTime": "OPEN"|"OPEN_30"|"OPEN_60" (default OPEN, only matters when AMO=True)
    }
    """
    symbol = (payload.get("symbol") or "").strip().upper()
    quantity = payload.get("quantity")
    transaction_type = (payload.get("transactionType") or "BUY").upper()
    product_type = (payload.get("productType") or "INTRADAY").upper()
    order_type = (payload.get("orderType") or "MARKET").upper()
    price = float(payload.get("price") or 0)
    trigger_price = float(payload.get("triggerPrice") or 0)
    after_market_order = bool(payload.get("afterMarketOrder", False))
    amo_time = str(payload.get("amoTime") or "OPEN").upper()

    # Validate
    if not symbol:
        raise HTTPException(status_code=400, detail="symbol required")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
        raise HTTPException(status_code=400, detail="quantity must be a positive integer")
    if transaction_type not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="transactionType must be BUY or SELL")
    if product_type not in ("INTRADAY", "CNC"):
        raise HTTPException(status_code=400, detail="productType must be INTRADAY or CNC")
    if order_type not in ("MARKET", "LIMIT", "SL", "SL_M", "SL_L"):
        raise HTTPException(status_code=400, detail="orderType must be MARKET, LIMIT, SL, SL_M or SL_L")
    if order_type in ("LIMIT", "SL_L") and price <= 0:
        raise HTTPException(status_code=400, detail="price > 0 required for LIMIT / SL_L orders")
    if order_type in ("SL", "SL_M", "SL_L") and trigger_price <= 0:
        raise HTTPException(status_code=400, detail="triggerPrice > 0 required for SL / SL_M / SL_L orders")
    if amo_time not in ("OPEN", "OPEN_30", "OPEN_60"):
        raise HTTPException(status_code=400, detail="amoTime must be OPEN, OPEN_30 or OPEN_60")

    order_fn, broker = _order_broker()
    result = order_fn(
        symbol=symbol,
        quantity=quantity,
        transaction_type=transaction_type,
        product_type=product_type,
        order_type=order_type,
        price=price,
        trigger_price=trigger_price,
        after_market_order=after_market_order,
        amo_time=amo_time,
    )
    # Mirror broker order-fn's .success to HTTP status — caller can read .error on 4xx/5xx.
    if isinstance(result, dict) and not result.get("success") and result.get("symbol") == symbol:
        # Common rejection paths (Symbol not found, Invalid quantity, HTTP non-200) — keep 200 and let client decide.
        # Only blow up to 502 if uvicorn couldn't reach Dhan at all.
        if isinstance(result.get("error"), str) and result["error"].startswith("Exception:"):
            raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/api/orders/place-batch")
def place_order_batch_endpoint(payload: dict):
    """Batch placement for Auto Buy. Hard cap: 5 orders (user policy).

    Each order gets submitted via place_dhan_order in parallel using
    a ThreadPoolExecutor sized to the batch (max 5) so we finish quickly
    but never burst-rate-limit Dhan's order API. Per-order outcomes are
    returned — partial successes are normal (e.g. one symbol missing
    from the instrument master), caller should surface succeeded/failed
    counts and per-symbol errors.
    """
    orders = payload.get("orders") or []
    source = (payload.get("source") or "auto_buy")

    if not isinstance(orders, list) or len(orders) == 0:
        raise HTTPException(status_code=400, detail="orders list required (1-5 items)")
    if len(orders) > 5:
        raise HTTPException(
            status_code=400,
            detail=f"Auto-buy cap is 5 (got {len(orders)}). Selecting top-5 rows only.",
        )

    validated: list[dict] = []
    for i, o in enumerate(orders):
        symbol = (o.get("symbol") or "").strip().upper()
        quantity = o.get("quantity")
        if not symbol:
            raise HTTPException(status_code=400, detail=f"orders[{i}]: symbol required")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise HTTPException(status_code=400, detail=f"orders[{i}]: quantity must be a positive integer")
        sl_raw = o.get("stopLoss")
        sl_trigger = float(sl_raw) if sl_raw and float(sl_raw) > 0 else 0
        validated.append({
            "symbol": symbol,
            "quantity": quantity,
            "transaction_type": (o.get("transactionType") or "BUY").upper(),
            "product_type":   (o.get("productType")   or "INTRADAY").upper(),
            "after_market_order": bool(o.get("afterMarketOrder", False)),
            "amo_time": str(o.get("amoTime") or "OPEN").upper(),
            "stop_loss": sl_trigger,
        })

    order_fn, broker = _order_broker()

    def submit_one(order):
        # Pre-filter cautionary listings — skip without calling broker API
        _tradable, _reason = is_stock_tradable(order["symbol"], "NSE")
        if not _tradable:
            return {
                "success": False,
                "error": f"Cautionary listing — order blocked by exchange",
                "symbol": order["symbol"],
            }
        buy_result = order_fn(
            symbol=order["symbol"],
            quantity=order["quantity"],
            transaction_type=order["transaction_type"],
            product_type=order["product_type"],
            after_market_order=order["after_market_order"],
            amo_time=order["amo_time"],
        )
        # If buy succeeded and a stop-loss trigger was provided, place SL-M sell
        if buy_result and buy_result.get("success") and order.get("stop_loss", 0) > 0:
            try:
                sl_fn, _ = _order_broker()
                sl_result = sl_fn(
                    symbol=order["symbol"],
                    quantity=order["quantity"],
                    transaction_type="SELL",
                    product_type=order["product_type"],
                    order_type="SL_M",
                    trigger_price=order["stop_loss"],
                    price=0,
                    after_market_order=False,
                    amo_time="OPEN",
                )
                if sl_result and sl_result.get("success"):
                    sl_order_id = sl_result.get("order_id")
                    buy_result["sl_order_id"] = sl_order_id
                    buy_result["sl_trigger"] = order["stop_loss"]
                    # Store SL metadata so modify_angel_order can trail it
                    try:
                        from broker.angel_margin_calculator import resolve_symbol_token as _resolve_sl_token
                        _tradingsym, _token = _resolve_sl_token(order["symbol"], "NSE")
                        register_sl_order(sl_order_id, {
                            "tradingsymbol": _tradingsym or f"{order['symbol']}-EQ",
                            "token": str(_token or ""),
                            "exchange": "NSE",
                            "transaction_type": "SELL",
                            "product_type": order["product_type"],
                            "order_type": "STOPLOSS_MARKET",
                            "quantity": order["quantity"],
                            "price": "0",
                            "variety": "STOPLOSS",
                        })
                    except Exception as reg_exc:
                        print(f"⚠️ Failed to register SL metadata: {reg_exc}")
                else:
                    buy_result["sl_error"] = (sl_result or {}).get("error", "SL placement failed")
            except Exception as sl_exc:
                buy_result["sl_error"] = str(sl_exc)
        return buy_result

    workers = min(5, len(validated))
    results: list = [None] * len(validated)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(submit_one, o): i for i, o in enumerate(validated)}
        for fut in as_completed(futures):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as exc:
                results[idx] = {"success": False, "error": str(exc), "symbol": validated[idx]["symbol"]}

    succeeded = sum(1 for r in results if r and r.get("success"))
    sl_placed = sum(1 for r in results if r and r.get("sl_order_id"))
    return {
        "source": source,
        "total": len(validated),
        "succeeded": succeeded,
        "failed": len(validated) - succeeded,
        "slPlaced": sl_placed,
        "results": results,
    }


# ================================================================
# TRAILING SL ENDPOINT (for Big Players auto trail)
# ================================================================

_bp_sl_orders: dict[str, dict] = {}  # symbol → {slOrderId, trigger, entryPrice}

@app.post("/api/orders/trail-sl")
def trail_sl_endpoint(payload: dict):
    """Move an existing SL order's trigger price up (trailing SL).

    Angel One's modifyOrder API updates the trigger price in place —
    no cancel/replace needed.  We store the SL order metadata on first
    placement (in place-batch) so this endpoint has all required fields
    without a getOrderBook call.

    Args (JSON body):
        symbol (str): Stock symbol.
        order_id (str): Existing SL order ID to trail.
        new_trigger (float): New trigger price (must be higher).
        quantity (int, optional): Quantity (defaults to stored).
    """
    symbol = ((payload.get("symbol") or "")).strip().upper()
    order_id = str(payload.get("order_id") or "")
    new_trigger = float(payload.get("new_trigger") or 0)

    if not symbol or not order_id or new_trigger <= 0:
        raise HTTPException(status_code=400, detail="symbol, order_id, and new_trigger (>0) required")

    result = modify_angel_order(
        order_id=order_id,
        symbol=symbol,
        new_trigger=new_trigger,
        new_quantity=payload.get("quantity"),
    )

    if result.get("success"):
        # Update the frontend-accessible store with new trigger
        _bp_sl_orders[symbol] = {
            "slOrderId": order_id,
            "trigger": new_trigger,
        }

    return result


@app.get("/api/health")
def health():
    return {"status": "healthy"}


# ================================================================
# P3 — LIVE MARKET TICKS (from Angel One WebSocket)
# ================================================================

@app.get("/api/market/live-ticks")
def get_live_ticks():
    """Return latest tick data for all subscribed symbols."""
    is_conn = (angel_is_connected() and angel_ws_connected()) or dhan_feed_connected()
    return {"connected": is_conn, "ticks": _build_ticks_by_symbol()}


@app.get("/api/market/live-ticks/stream")
async def stream_live_ticks(request: Request):
    """Server-Sent Events endpoint — pushes tick data every ~250 ms.

    The frontend opens a single EventSource connection and receives
    ``{"connected": true, "ticks": {…}}`` JSON payloads as ``data``
    lines whenever the tick dict changes, instead of polling every 5 s.
    """
    import json as _json

    async def _generate():
        last_digest = ""
        while True:
            try:
                is_conn = (angel_is_connected() and angel_ws_connected()) or dhan_feed_connected()
                payload = _json.dumps({
                    "connected": is_conn,
                    "ticks": _build_ticks_by_symbol(),
                })
                digest = hashlib.md5(payload.encode()).hexdigest()
                if digest != last_digest:
                    last_digest = digest
                    yield f"data: {payload}\n\n"
                else:
                    # Regular data line (not a comment) so onmessage fires
                    # on the frontend — this resets the watchdog that detects
                    # silent disconnects from screen-sleep.
                    yield f"data: {{\"connected\": {_json.dumps(is_conn)}}}\n\n"
            except Exception:
                yield f"data: {_json.dumps({'connected': False, 'ticks': {}})}\n\n"
            await asyncio.sleep(0.50)  # 500 ms (was 250 — no need to hammer)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/strategies/advanceorb/refresh")
def refresh_advance_orb(tickers: str = "", timeframe: int = 5):
    """Lightweight refresh of price/volume/change for a fixed list of tickers.

    Re-checks the 9:15 opening-candle ≤1.5% eligibility and the open-vs-
    previous-high band.  The first 5-minute candle may still be forming when
    the initial screener request runs, so a row that initially passes can
    later exceed the 1.5% range.  Such rows must be removed before auto-buy
    evaluates.
    """
    try:
        symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if not symbols:
            return {"refreshed": []}

        opening_candle_map: dict | None = None
        # Re-check ≤1.5% opening-candle rule
        tv_candles = batch_tv_opening_candles(symbols, timeframe=int(timeframe))
        opening_candle_map = {}
        for symbol in symbols:
            candle = tv_candles.get(symbol)
            if candle:
                opn, high, low, close, yh = candle
                rng = ((high - low) / low) * 100
                opening_candle_map[symbol] = (
                    rng <= SMALL_CANDLE_THRESHOLD, high, opn, low,
                    close, rng, None, yh, None, None,
                )
            else:
                opening_candle_map[symbol] = (
                    False, None, None, None, None, None, None, None, None, None
                )
        # Eligibility = small 9:15 candle AND open within ±2% of
        # yesterday's high (TV daily high).  Same rule as the table.
        valid_symbols = set()
        for symbol, candle in opening_candle_map.items():
            if not (isinstance(candle, tuple) and candle and candle[0]):
                continue
            opn_v, yh_v = candle[2], candle[7]
            if pd.notna(opn_v) and pd.notna(yh_v) and yh_v and float(yh_v) > 0:
                lo_b = 0.98 * float(yh_v)
                hi_b = 1.02 * float(yh_v)
                if not (lo_b <= float(opn_v) <= hi_b):
                    continue
            valid_symbols.add(symbol)
        # If the chart-feed candle is not available at all (deferred /
        # TradingView unreachable), don't drop rows based on it.
        if not valid_symbols and not any(
            tv_candles.get(s) is not None for s in symbols
        ):
            valid_symbols = set(symbols)

        # TV SCAN COMMENTED OUT for WS-only testing (Jul 31).
        # tv_query = (Query().select(...)...)
        #
        # Instead: pull data directly from latest_ticks (saved + live).
        ws_ticks = {}
        raw = angel_ws_ticks()
        for token, d in raw.items():
            sym = d.get("symbol", "")
            if sym:
                ws_ticks[sym] = d

        cache_data = candle_tracker._cache if hasattr(candle_tracker, '_cache') else {}

        refreshed: list[dict] = []
        for name in symbols:
            if name not in valid_symbols:
                continue

            ws = ws_ticks.get(name)
            if not ws:
                continue

            close_val = float(ws.get("ltp", 0))
            change_val = ws.get("change_pct", 0)
            vol_raw = ws.get("volume", 0)

            if close_val <= 0:
                continue

            if pd.notna(vol_raw) and vol_raw >= 1_000_000:
                    vol_raw = int(ws_vol)

            if pd.notna(vol_raw) and vol_raw >= 1_000_000:
                volume_str = f"{vol_raw/1_000_000:.1f}M"
            elif pd.notna(vol_raw) and vol_raw >= 1_000:
                volume_str = f"{vol_raw/1_000:.1f}K"
            elif pd.notna(vol_raw):
                volume_str = str(int(vol_raw))
            else:
                volume_str = "0"
            relvol_str = f"{values[4]:.2f}x" if len(values) > 4 and pd.notna(values[4]) else "0x"

            # Pull live 9:20 candle data from the TradingView 5-min map
            candle = (opening_candle_map or {}).get(name)
            inside_915_val = False
            close920_val = None
            if isinstance(candle, tuple) and len(candle) >= 10:
                inside_915_val = bool(candle[9])
                close920_val = round(float(candle[8]), 2) if candle[8] is not None else None

            refreshed.append({
                "Symbol": name,
                "Price": round(float(close_val), 2) if pd.notna(close_val) else None,
                "CHG%": round(float(change_val), 2) if pd.notna(change_val) else None,
                "Volume": volume_str,
                "RELVOL": relvol_str,
                "inside_915": inside_915_val,
                "close920": close920_val,
            })

        return {"refreshed": refreshed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Serve only the frontend assets from the same origin as the API. Do not expose
# the repository root, which also contains backend source and project metadata.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(PROJECT_ROOT / "index.html", media_type="text/html")


@app.get("/style.css", include_in_schema=False)
def stylesheet():
    return FileResponse(PROJECT_ROOT / "style.css", media_type="text/css")


@app.get("/login.html", include_in_schema=False)
def login_page():
    return FileResponse(PROJECT_ROOT / "login.html", media_type="text/html")


app.mount("/js", StaticFiles(directory=PROJECT_ROOT / "js"), name="frontend-js")


# =================================================================
# AUTH (Supabase) — public config + JWT verification for the app shell
# =================================================================
# - /login             — serves the standalone /login page.
# - /api/auth/config   — anon key + URL. Safe to expose (anon key is
#                        meant to be public; Supabase RLS guards data).
# - /api/me            — verifies the Supabase bearer token by doing a
#                        GET on `${SUPABASE_URL}/auth/v1/user` with the
#                        service-role key as apikey. 503 if server-side
#                        role key is missing; 401 if the user JWT is
#                        bad/expired. Future endpoints (e.g. /api/me/
#                        settings) will piggyback on this lookup.
# (Auth surface stripped — no /login, no /api/auth/*, no /api/me/profile.)


# =================================================================
# BROKER CONNECTION (Dhan) — user-supplied creds via popup
# =================================================================
# The screener Settings tab has a "Select Broker" dropdown. Picking
# Dhan opens a popup (brokerModalOverlay in index.html) where the
# user enters `client_id` and `totp_secret`. submitBrokerCreds()
# POSTs them here. We then:
#   1. validate input
#   2. mint a TOTP code via `pyotp.TOTP(totp_secret).now()`
#   3. POST to https://auth.dhan.co/app/generateAccessToken with
#      userId/pin/totp to mint today's access_token
#   4. Stash everything in broker.quantity_calculator._DHAN_CREDS
#      via set_dhan_credentials + set_dhan_access_token
#
# From that moment, every refresh that hits /v2/margincalculator
# (heavy screener refresh + lightweight budget stepper) and
# /v2/orders (place_dhan_order from /api/orders/place) reads the
# active token through DHAN_ACCESS_TOKEN (which now resolves
# through the `_Cred` proxy in broker/quantity_calculator.py).
#
# Why this is a server endpoint, not pure client-side:
#   - The TOTP secret MUST be sent over the wire — even on a
#     trusted local connection this is a footgun, so /api/broker/
#     connect logs nothing that could be recovered from logs.
#   - Dhan's generateAccessToken rejects browser CORS preflights
#     even with allow_origins (they only allow server probes).
#     Going server-side is the only viable path.
#
# /api/broker/status is called by js/settings.js on page load to
# refresh the green/red status badge next to the dropdown.
# /api/broker/disconnect is wired to the "Disconnect" button.

# ================================================================
# PORTFOLIO — real-time funds + holdings from connected broker
# ================================================================

@app.get("/api/portfolio/funds")
def portfolio_funds():
    """Fetch available funds/margin from the connected broker.

    Returns the active broker's fund limit so the portfolio page
    can show real-time available budget.

    Response: {"success": bool, "broker": str, "data": dict|None, "error": str|None}
    """
    from broker.dhan_holdings import get_dhan_fund_limit
    from broker.angel_holdings import get_angel_fund_limit

    dhan_tok = _broker_cred("access_token")
    if dhan_tok and _broker_cred("client_id"):
        result = get_dhan_fund_limit()
        result["broker"] = "dhan"
        return result

    if angel_is_connected():
        result = get_angel_fund_limit()
        result["broker"] = "angel"
        return result

    return {"success": False, "broker": None, "data": None, "error": "No broker connected"}


@app.get("/api/portfolio/holdings")
def portfolio_holdings():
    """Fetch current holdings from the connected broker.

    Returns list of holding positions for the portfolio page.

    Response: {"success": bool, "broker": str, "data": list|None, "error": str|None}
    """
    from broker.dhan_holdings import get_dhan_holdings
    from broker.angel_holdings import get_angel_holdings

    dhan_tok = _broker_cred("access_token")
    if dhan_tok and _broker_cred("client_id"):
        result = get_dhan_holdings()
        result["broker"] = "dhan"
        return result

    if angel_is_connected():
        result = get_angel_holdings()
        result["broker"] = "angel"
        return result

    return {"success": False, "broker": None, "data": [], "error": "No broker connected"}


@app.get("/api/portfolio/positions")
def portfolio_positions():
    """Fetch open positions from the connected broker.

    Returns intraday + carry-forward positions so the portfolio
    table can show real per-stock P&L, quantity, entry price etc.

    Response: {"success": bool, "broker": str, "data": list|None, "error": str|None}
    """
    from broker.dhan_holdings import get_dhan_positions
    from broker.angel_holdings import get_angel_positions

    dhan_tok = _broker_cred("access_token")
    if dhan_tok and _broker_cred("client_id"):
        result = get_dhan_positions()
        result["broker"] = "dhan"
        return result

    if angel_is_connected():
        result = get_angel_positions()
        result["broker"] = "angel"
        return result

    return {"success": False, "broker": None, "data": [], "error": "No broker connected"}


@app.post("/api/broker/connect")
def broker_connect(payload: dict):
    broker = (payload.get("broker") or "").strip().lower()

    # ======================== DHAN ========================
    if broker == "dhan":
        client_id   = (payload.get("client_id")   or "").strip()
        totp_secret = (payload.get("totp_secret") or "").strip()
        pin         = (payload.get("pin")         or "").strip()

        if not client_id or not totp_secret:
            raise HTTPException(
                status_code=400,
                detail="client_id and totp_secret are required.",
            )

        try:
            totp_code = pyotp.TOTP(totp_secret).now()
        except Exception as e:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid TOTP secret (pyotp rejected it): {e}",
            )

        set_dhan_credentials(client_id, pin, totp_secret, broker_name="dhan")

        try:
            r = requests.post(
                "https://auth.dhan.co/app/generateAccessToken",
                params={
                    "dhanClientId": client_id,
                    "pin":          pin,
                    "totp":         totp_code,
                },
                proxies=DHAN_PROXIES,
                timeout=10,
            )
        except Exception as e:
            return {"ok": False, "connected": False, "broker": "dhan",
                    "detail": f"network error: {e}"}

        if r.status_code == 200 and r.text:
            try:
                data = r.json()
            except Exception:
                data = {}
            token = (data.get("accessToken") or data.get("access_token")
                     or "").strip()
            if token:
                set_dhan_access_token(token)
                # Start the Dhan tick-by-tick market feed now that we have
                # credentials. It auto-falls back to Angel WS if Dhan's
                # Data API subscription is missing.
                if dhan_feed_start:
                    try:
                        dhan_feed_start()
                    except Exception as e:
                        print(f"⚠️ Dhan feed start error: {e}")
                return {"ok": True, "connected": True, "broker": "dhan"}

        return {
            "ok": False,
            "connected": False,
            "broker": "dhan",
            "status_code": r.status_code,
            "detail": (r.text or "")[:200],
        }

    # ====================== ANGEL ONE ======================
    if broker == "angel":
        api_key    = (payload.get("api_key")    or "").strip()
        client_id  = (payload.get("client_id")  or "").strip()
        password   = (payload.get("password")   or "").strip()
        totp_secret = (payload.get("totp_secret") or "").strip()

        if not api_key or not client_id or not password:
            raise HTTPException(
                status_code=400,
                detail="api_key, client_id, and password are required for Angel One.",
            )

        set_angel_credentials(api_key, client_id, password, totp_secret or None)
        auth_result = angel_authenticate()

        if auth_result.get("ok"):
            # ── P1: Auto-start WebSocket after successful Angel One auth ──
            # Seed a minimal watchlist so the initial on_open subscription
            # doesn't fire with an empty list (which would skip all subs).
            # Real symbols get added later via ws_auto_subscribe when the
            # screener runs.
            try:
                feed_token = _angel_creds.get("feed_token", "")
                from broker.angel_margin_calculator import resolve_symbol_token as _resolve
                # Subscribe ALL 727 stocks at WebSocket startup so the CandleTracker
                # receives ticks for every symbol in the watchlist.
                # Build the list from the candle_tracker's token→symbol map.
                initial_ws = [
                    ("NIFTY", 26000, "index"),
                ]
                _all_stocks = [
                    (sym, int(tok), "stock")
                    for sym, tok in candle_tracker.token_by_symbol.items()
                ]
                initial_ws.extend(_all_stocks)
                ws_res = angel_ws_start(feed_token=feed_token, watchlist=initial_ws)
                if ws_res.get("success"):
                    print("✅ WebSocket connected successfully")
                else:
                    print(f"⚠️ WebSocket start: {ws_res.get('error')}")
                # Also start the Dhan tick-by-tick feed (if creds exist) as
                # the preferred source; Angel WS remains the automatic fallback.
                if dhan_feed_start:
                    try:
                        dhan_feed_start()
                    except Exception as e:
                        print(f"⚠️ Dhan feed start error: {e}")
            except Exception as e:
                print(f"⚠️ WebSocket start error: {e}")

            return {
                "ok": True,
                "connected": True,
                "broker": "angel",
                "client_id_masked": (
                    client_id[:2] + "***" + client_id[-2:]
                    if len(client_id) > 4 else "****"
                ),
            }

        return {
            "ok": False,
            "connected": False,
            "broker": "angel",
            "detail": auth_result.get("error", "Angel One authentication failed"),
        }

    # ===================== UNSUPPORTED =====================
    raise HTTPException(
        status_code=400,
        detail=f"Broker {broker!r} not supported in this build. Pick Dhan or angel.",
    )


# ❌ /api/export-watchlist removed — Angel One WAF blocks watchlist REST API.

@app.post("/api/broker/disconnect")
def broker_disconnect():
    angel_ws_stop()
    clear_dhan_credentials()
    angel_disconnect()
    return {"ok": True, "connected": False}


@app.post("/api/broker/refresh-token")
def broker_refresh_token():
    """Manually trigger /RenewToken right now. Mostly used as a
    'kick the auto-renew loop' button if the daily renewal ever
    fails — the auto-loop calls the same helper on schedule."""
    return _dhan_renew()


# ================================================================
# PORTFOLIO / HOLDINGS API
# ================================================================

@app.get("/api/broker/status")
def broker_status():
    # Dhan status
    dhan_cid = _broker_cred("client_id")
    dhan_tok = _broker_cred("access_token")
    dhan_connected = bool(dhan_tok) and bool(dhan_cid)

    # Angel One status
    angel_connected = angel_is_connected()
    angel_cid = _angel_creds.get("client_id", "") if angel_connected else ""

    # Return the active broker first; if both are connected,
    # Dhan takes priority (legacy contract).
    if dhan_connected:
        cid = dhan_cid
        return {
            "connected": True,
            "broker": "dhan",
            "client_id_masked":
                ("*" * (len(cid) - 4) + cid[-4:])
                if cid and len(cid) > 4 else None,
        }
    if angel_connected:
        cid = angel_cid
        return {
            "connected": True,
            "broker": "angel",
            "client_id_masked":
                (cid[:2] + "***" + cid[-2:])
                if cid and len(cid) > 4 else "****",
        }
    return {"connected": False}


@app.get("/api/cache/status")
def cache_status():
    """Return strategy cache status for the frontend indicator."""
    from server.candle_tracker import candle_tracker as _ct
    return _ct.get_cache_status()


@app.post("/api/data/purge-old-day")
def purge_old_day():
    """Manually trigger the daily old-day purge: candles.json is rewritten
    to contain today's data only, so prior-day rows are physically deleted.
    Also runs automatically every morning at date rollover and at startup."""
    from server.candle_tracker import candle_tracker as _ct
    try:
        result = _ct.purge_old_day_data()
        return {"success": True, **result}
    except Exception as e:
        return {"success": False, "error": str(e)}


# =================================================================
# SPA FALLBACK — any GET that doesn't match an explicit route or
# a static-asset mount, and isn't under /api/ /js/ /style.css,
# returns index.html. Lets the user paste a deep link like
# /settings, refresh inside the settings tab, or land on any tab
# via the in-app router — and get the SPA, not a 404.
# =================================================================
@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    # 404 the things that genuinely should 404 (so static typos
    # surface clearly instead of getting the dashboard).
    if full_path.startswith(("api/", "js/", "style.css")):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(PROJECT_ROOT / "index.html", media_type="text/html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)