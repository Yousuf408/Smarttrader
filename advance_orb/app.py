from pathlib import Path

import asyncio
import time
import requests
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
from broker.angel_orders import place_angel_order
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
from advance_orb.angel_watchlist import export_symbols as angel_wl_export

# =================================================================
# No login flow, no Supabase, no JWT — the auth surface has been
# stripped. All API endpoints in this app are open and read/write
# the screener's in-memory caches. Frontend is served as static
# files from PROJECT_ROOT (see secure-static-hosting.md for the
# why-we-don't-mount-repo-root rule).
# =================================================================


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


@asynccontextmanager
async def lifespan(_app):
    task = asyncio.create_task(_dhan_auto_renew_loop())
    print("[broker] daily auto-renew loop started")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
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
    SMALL_CANDLE_THRESHOLD, EMA_SPAN, EMA_LOOKBACK_DAYS,
    IST, MAX_TV_STOCKS, YFINANCE_WORKERS,
    has_small_opening_candle, compute_200_ema, compute_200_ema_batch,
    batch_opening_candle, filter_small_opening_candles,
    _detect_trading_date, _calc_qty_for_broker,
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
    "MaxQty",
]

GAP_UP_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "GAP%",
    "Volume",
    "RELVOL",
    "Sector",
    "200 EMA",
    "Open 9:15",
    "Prev High",
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
def get_advance_orb(budget: int = 100000, parts: int = 4, gap_up: bool = False):
    """
    Fetch stocks from TradingView with 4 conditions:
    1. Price: 200 to 3000 INR
    2. Gap: < 2%
    3. Market Cap: > 41B INR
    4. Exchange: NSE

    Query params:
      budget: total capital in INR (default 100000)
      parts:  number of equal parts to split budget into (default 4)
    """
    if budget <= 0:
        raise HTTPException(status_code=400, detail="budget must be > 0")
    if parts < 1 or parts > 20:
        raise HTTPException(status_code=400, detail="parts must be between 1 and 20")
    try:
        # ─── Step 1: Fetch from TradingView ───
        # Single POST to TradingView's scan endpoint, capped to MAX_TV_STOCKS
        # rows (≈4 pages of the default 50-row page size).
        tv_columns = [
            'name', 'close', 'change', 'gap', 'volume',
            'relative_volume', 'market_cap_basic', 'sector',
        ]
        tv_query = (Query()
            .select(*tv_columns)
            .set_markets('india')
            .where(
                col('close') > PRICE_MIN,
                col('close') <= PRICE_MAX,
                col('market_cap_basic') > MARKET_CAP_MIN,
                col('exchange') == 'NSE'
            )
            .order_by('change', ascending=False)
        )

        body = dict(tv_query.query)
        body['range'] = [0, MAX_TV_STOCKS]
        response = requests.post(
            tv_query.url,
            json=body,
            headers=TV_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        total = int(payload.get('totalCount') or 0)

        raw_rows: list[dict] = []
        for symbol_row in (payload.get('data') or [])[:MAX_TV_STOCKS]:
            values = symbol_row.get('d') or []
            ticker = symbol_row.get('s')
            raw_rows.append(dict(zip(['ticker', *tv_columns], [ticker, *values])))

        df = pd.DataFrame(raw_rows)
        count = min(total, MAX_TV_STOCKS)

        if count == 0:
            return {
                "strategy": "advanceorb",
                "name": "Advance ORB",
                "count": 0,
                "data": [],
                "columns": ADVANCE_ORB_COLUMNS,
                "message": "No stocks found matching the conditions"
            }

        # ─── Step 2: Apply Gap Filter (< 2%) ───
        df['gap'] = pd.to_numeric(df['gap'], errors='coerce')
        df = df[df['gap'].notna() & (abs(df['gap']) < GAP_THRESHOLD)]

        if df.empty:
            return {
                "strategy": "advanceorb",
                "name": "Advance ORB",
                "count": 0,
                "data": [],
                "columns": ADVANCE_ORB_COLUMNS,
                "message": f"No stocks with gap < {GAP_THRESHOLD}%"
            }

        # ─── Step 3: Format Data for Frontend ───
        # Run the Yahoo Finance candle check across every candidate in parallel.
        # In normal mode, keep only rows whose ticker passes the ≤1.5% range check.
        # In gap_up mode, skip the candle-range filter entirely.
        candidate_symbols = df['name'].dropna().astype(str).tolist()

        # Open-candle batch: pull each symbol's 9:15 IST 5-min candle in
        # parallel. Returns (is_small, high915, open915, low915,
        # close915, range_pct) per symbol.
        #   * is_small   — used by the small-open-candle gate below
        #   * high915    — read by the auto-buy band-filter on the frontend
        # (open_val is returned for future use but no longer consumed here.)
        opening_candle_map = batch_opening_candle(candidate_symbols)
        small_candle_symbols = {
            s for s, t in opening_candle_map.items()
            if isinstance(t, tuple) and t and t[0]
        }
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
        df['open915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None))[2]
        )
        df['candle_range_pct'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None))[5]
        )
        df['yesterday_high'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None, None, None, None, None))[7]
        )
        # Compute 200-period EMA per candidate in parallel; surface
        # in df['ema']. NOT a screener filter — the auto-buy frontend
        # has the additional `price > ema` gate. Missing EMA simply
        # yields None in the row; auto-buy treats None as 'skip the
        # candidate' (never commit on unvalidated data).
        ema_map = compute_200_ema_batch(candidate_symbols)
        df['ema'] = df['name'].map(ema_map)

        # Calculate Max Quantities via Dhan (see broker/quantity_calculator.py).
        # quantity_calculator expects Symbol/Price cols; df has name/close.
        # Token from broker runtime store (populated via the broker
        # popup / /api/broker/connect). Empty string falls through
        # so quantity_calculator.requests call hits the missing-token
        # code path and the screener surfaces a clear "no broker"
        # error to the UI instead of a silent bare 500.
        _calc_qty_for_broker(df, budget, parts)

        result = []
        for _, row in df.iterrows():
            symbol = row['name']

            if gap_up:
                # Gap-up mode: skip 1.5% candle filter.
                # Instead require the 9:15 OPENING price > 200 EMA AND
                # opening price > yesterday's high (gap-up at the open).
                open915 = row.get('open915')
                ema_val = row.get('ema')
                yh = row.get('yesterday_high')
                if not pd.notna(open915) or open915 <= 0:
                    continue
                if not pd.notna(ema_val) or not pd.notna(yh):
                    continue
                if float(open915) <= float(ema_val) or float(open915) <= float(yh):
                    continue
            else:
                # Normal mode: filter by small 9:15 candle (≤1.5% range)
                if symbol not in small_candle_symbols:
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

            if not gap_up:
                # Normal mode: include candle detail columns
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

            result.append(entry)

        out_columns = GAP_UP_COLUMNS if gap_up else ADVANCE_ORB_COLUMNS
        conditions = {
            "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
            "gap": f"< {GAP_THRESHOLD}%",
            "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
            "exchange": "NSE",
        }
        if gap_up:
            conditions["filter"] = "Price > 200 EMA AND Price > Prev High"
        else:
            conditions["small_candle"] = f"9:15 IST range <= {SMALL_CANDLE_THRESHOLD}%"

        return {
            "strategy": "advanceorb",
            "name": "Advance ORB",
            "count": len(result),
            "data": result,
            "columns": out_columns,
            "conditions": conditions,
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
      "afterMarketOrder": bool (default False),
      "amoTime": "OPEN"|"OPEN_30"|"OPEN_60" (default OPEN, only matters when AMO=True)
    }
    """
    symbol = (payload.get("symbol") or "").strip().upper()
    quantity = payload.get("quantity")
    transaction_type = (payload.get("transactionType") or "BUY").upper()
    product_type = (payload.get("productType") or "INTRADAY").upper()
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
    if amo_time not in ("OPEN", "OPEN_30", "OPEN_60"):
        raise HTTPException(status_code=400, detail="amoTime must be OPEN, OPEN_30 or OPEN_60")

    order_fn, broker = _order_broker()
    result = order_fn(
        symbol=symbol,
        quantity=quantity,
        transaction_type=transaction_type,
        product_type=product_type,
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
        validated.append({
            "symbol": symbol,
            "quantity": quantity,
            "transaction_type": (o.get("transactionType") or "BUY").upper(),
            "product_type":   (o.get("productType")   or "INTRADAY").upper(),
            "after_market_order": bool(o.get("afterMarketOrder", False)),
            "amo_time": str(o.get("amoTime") or "OPEN").upper(),
        })

    order_fn, broker = _order_broker()

    def submit_one(order):
        return order_fn(
            symbol=order["symbol"],
            quantity=order["quantity"],
            transaction_type=order["transaction_type"],
            product_type=order["product_type"],
            after_market_order=order["after_market_order"],
            amo_time=order["amo_time"],
        )

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
    return {
        "source": source,
        "total": len(validated),
        "succeeded": succeeded,
        "failed": len(validated) - succeeded,
        "results": results,
    }


@app.get("/api/health")
def health():
    return {"status": "healthy"}


# ================================================================
# P3 — LIVE MARKET TICKS (from Angel One WebSocket)
# ================================================================

@app.get("/api/market/live-ticks")
def get_live_ticks():
    """Return latest tick data for all subscribed symbols."""
    if not angel_is_connected() or not angel_ws_connected():
        return {"connected": False, "ticks": {}}
    return {"connected": True, "ticks": _build_ticks_by_symbol()}


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
                if not angel_is_connected() or not angel_ws_connected():
                    payload = _json.dumps({"connected": False, "ticks": {}})
                else:
                    payload = _json.dumps({"connected": True, "ticks": _build_ticks_by_symbol()})
                digest = hashlib.md5(payload.encode()).hexdigest()
                if digest != last_digest:
                    last_digest = digest
                    yield f"data: {payload}\n\n"
                else:
                    yield ": heartbeat\n\n"  # SSE comment → keeps connection alive
            except Exception:
                yield f"data: {_json.dumps({'connected': False, 'ticks': {}})}\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/strategies/advanceorb/refresh")
def refresh_advance_orb(tickers: str = "", gap_up: bool = False):
    """Lightweight refresh of price/volume/change for a fixed list of tickers.

    In normal mode, re-checks the 9:15 opening-candle ≤1.5% eligibility.
    The first 5-minute candle may still be forming when the initial screener
    request runs, so a row that initially passes can later exceed the 1.5%
    range. Such rows must be removed before auto-buy evaluates.
    In gap_up mode, the candle-range check is skipped.
    """
    try:
        symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if not symbols:
            return {"refreshed": []}

        if gap_up:
            # Gap-up mode: skip the candle-range check, keep all symbols valid
            valid_symbols = set(symbols)
        else:
            # Normal mode: re-check ≤1.5% opening-candle rule
            opening_candle_map = batch_opening_candle(symbols)
            valid_symbols = {
                symbol for symbol, candle in opening_candle_map.items()
                if isinstance(candle, tuple) and candle and candle[0]
            }

        tv_query = (Query()
            .select('name', 'close', 'change', 'volume', 'relative_volume')
            .set_markets('india')
            .where(col('exchange') == 'NSE')
            .set_tickers(*[f'NSE:{sym}' for sym in symbols])
        )

        body = dict(tv_query.query)
        body['range'] = [0, max(50, len(symbols) + 10)]
        response = requests.post(
            tv_query.url,
            json=body,
            headers=TV_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

        # ── P3: Overlay WebSocket tick data when Angel One is connected ──
        ws_ticks = {}
        if angel_is_connected() and angel_ws_connected():
            raw = angel_ws_ticks()
            for token, d in raw.items():
                sym = d.get("symbol", "")
                if sym:
                    ws_ticks[sym] = d
            # Also subscribe any new symbols the frontend sent us
            ws_auto_subscribe(symbols)

        refreshed: list[dict] = []
        for symbol_row in (payload.get('data') or []):
            values = symbol_row.get('d') or []
            if len(values) < 5:
                continue
            name = values[0]
            if not name or name not in valid_symbols:
                continue

            # Default values from TV data (always available)
            close_val = pd.to_numeric(values[1], errors='coerce')
            change_val = pd.to_numeric(values[2], errors='coerce')
            vol_raw = pd.to_numeric(values[3], errors='coerce')

            # P3 — Overlay WebSocket tick data when available (freshest)
            ws = ws_ticks.get(name)
            if ws:
                ltp = ws.get("ltp")
                if ltp is not None:
                    close_val = float(ltp)
                chg_pct = ws.get("change_pct")
                if chg_pct is not None:
                    change_val = round(float(chg_pct), 2)
                ws_vol = ws.get("volume")
                if ws_vol is not None:
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

            refreshed.append({
                "Symbol": name,
                "Price": round(float(close_val), 2) if pd.notna(close_val) else None,
                "CHG%": round(float(change_val), 2) if pd.notna(change_val) else None,
                "Volume": volume_str,
                "RELVOL": relvol_str,
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
                # Add NIFTY 50 index as a default subscriber (always useful)
                initial_ws = [
                    ("NIFTY", 26000, "index"),
                ]
                ws_res = angel_ws_start(feed_token=feed_token, watchlist=initial_ws)
                if ws_res.get("success"):
                    print("✅ WebSocket connected successfully")
                else:
                    print(f"⚠️ WebSocket start: {ws_res.get('error')}")
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


@app.post("/api/export-watchlist")
async def export_watchlist(request: Request):
    """Export screener symbols to the TradeAlgo Pro watchlist on Angel One.

    Expects JSON body: {"symbols": ["RELIANCE", "TCS", "INFY", ...]}
    """
    import json
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except Exception:
            body = {}
    symbols = (body or {}).get("symbols", [])
    if not symbols or not isinstance(symbols, list):
        raise HTTPException(status_code=400, detail="symbols list is required")
    # Run the blocking watchlist calls in a thread so the event loop isn't stalled
    import asyncio
    return await asyncio.to_thread(angel_wl_export, symbols)


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