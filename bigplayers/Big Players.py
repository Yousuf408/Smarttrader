from pathlib import Path
from zoneinfo import ZoneInfo

import asyncio
import os
import re
import time
import requests
import pyotp
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
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

# ================================================================
# BIG PLAYERS STRATEGY IMPORT (only strategy left)
# ================================================================
from bigplayers.strategy import BigPlayersStrategy, calculate_breakout_status, calculate_support_price

# =================================================================
# No login flow, no Supabase, no JWT — the auth surface has been
# stripped. All API endpoints in this app are open and read/write
# the screener's in-memory caches. Frontend is served as static
# files from PROJECT_ROOT.
# =================================================================


# =================================================================
# DAILY-AUTO-RENEW BACKGROUND TASK
# =================================================================
async def _dhan_auto_renew_loop():
    while True:
        sleep_for = 60.0
        try:
            issued = _broker_cred("token_issued_at")
            tok    = _broker_cred("access_token")
            if issued and tok:
                age = time.time() - float(issued)
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
                age = time.time() - float(issued)
                remaining = max(0.0, renew_at_age - age)
                sleep_for = min(
                    max(30.0, remaining + 5.0),
                    15 * 60.0,
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
    title="TradeAlgo Pro - Big Players Strategy",
    description="Fetches NSE stocks with Big Players strategy (breakout & support)",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# No-cache middleware
@app.middleware("http")
async def _no_cache_all(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"]        = "no-cache"
    response.headers["Expires"]       = "0"
    return response

# ─── HARDCODED CONDITIONS ───
PRICE_MIN = 200
PRICE_MAX = 3000
GAP_THRESHOLD = 2.0
MARKET_CAP_MIN = 41_000_000_000  # 41 Billion INR
SMALL_CANDLE_THRESHOLD = 1.5

# ── 200-period EMA (used for support price) ──
EMA_SPAN = 200
EMA_LOOKBACK_DAYS = 4

IST = ZoneInfo("Asia/Kolkata")
MAX_TV_STOCKS = 200
YFINANCE_WORKERS = 8

BIG_PLAYERS_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "Breakout",
    "Support Price",
    "MaxQty",
]


# ================================================================
# SHARED HELPER FUNCTIONS (used by Big Players)
# ================================================================

def compute_200_ema(symbol: str):
    """200-period EMA on 5-min closes over the previous
    EMA_LOOKBACK_DAYS days, or None if Yahoo returns nothing usable.
    """
    if not symbol or not symbol.strip():
        return None
    ticker = symbol.strip() if symbol.strip().endswith(".NS") else f"{symbol.strip()}.NS"
    try:
        df = yf.download(ticker, period=f"{EMA_LOOKBACK_DAYS}d",
                         interval="5m", progress=False, auto_adjust=False)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(ticker, axis=1, level=-1)
            except (KeyError, IndexError):
                try:
                    df = df.xs(ticker, axis=1, level=0)
                except (KeyError, IndexError):
                    return None
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if closes.empty or len(closes) < EMA_SPAN:
            return None
        ema = closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
        return float(ema) if pd.notna(ema) else None
    except Exception:
        return None


def compute_200_ema_batch(symbols: list[str]) -> dict:
    """Parallel EMA fetch for an entire list of symbols."""
    results: dict = {}
    unique = list({s for s in symbols if s})
    if not unique:
        return results
    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(compute_200_ema, sym): sym for sym in unique}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result(timeout=20)
            except Exception:
                results[sym] = None
    return results


def batch_opening_candle(symbols: list[str]) -> dict:
    """
    For each symbol, fetch the 9:15 IST 5‑minute candle **only if it belongs to today**.
    Returns:
        (is_small, high915, open915, low915, close915, range_pct)
    If today's candle is not found, returns (False, None, None, None, None, None).
    """
    results: dict = {}
    unique = [s for s in {s for s in symbols if s}]
    if not unique:
        return results

    def _lookup(symbol: str):
        try:
            ticker = f"{str(symbol).strip().upper()}.NS"
            candles = yf.download(
                tickers=ticker,
                period="4d",
                interval="5m",
                progress=False,
                auto_adjust=False,
                prepost=False,
                threads=False,
            )
            if candles is None or candles.empty:
                return (False, None, None, None, None, None)

            if isinstance(candles.columns, pd.MultiIndex):
                try:
                    candles = candles.xs(ticker, axis=1, level=-1)
                except (KeyError, IndexError):
                    try:
                        candles = candles.xs(ticker, axis=1, level=0)
                    except (KeyError, IndexError):
                        return (False, None, None, None, None, None)

            if "High" not in candles or "Low" not in candles:
                return (False, None, None, None, None, None)

            # Convert index to IST
            local_index = pd.DatetimeIndex(candles.index)
            if local_index.tz is None:
                local_index = local_index.tz_localize('UTC').tz_convert(IST)
            else:
                local_index = local_index.tz_convert(IST)
            candles = candles.copy()
            candles.index = local_index

            today = pd.Timestamp.now(tz=IST).date()

            # Look for today's 9:15 candle (first 5‑minute bar after 9:15)
            opening_today = candles[
                (candles.index.date == today) &
                (candles.index.hour == 9) & (candles.index.minute >= 15)
            ]

            # If no candle today, return nothing (no fallback to previous day)
            if opening_today.empty:
                return (False, None, None, None, None, None)

            candle = opening_today.iloc[0]  # earliest today's 9:15+ candle

            high = pd.to_numeric(candle["High"], errors="coerce")
            low = pd.to_numeric(candle["Low"], errors="coerce")
            open915 = pd.to_numeric(candle["Open"], errors="coerce")
            close915 = pd.to_numeric(candle["Close"], errors="coerce")

            if pd.isna(high) or pd.isna(low) or low <= 0:
                return (False, None, None, None, None, None)

            candle_range_pct = ((float(high) - float(low)) / float(low)) * 100
            is_small = bool(candle_range_pct <= SMALL_CANDLE_THRESHOLD)

            return (
                is_small,
                float(high),
                float(open915) if pd.notna(open915) else None,
                float(low),
                float(close915) if pd.notna(close915) else None,
                float(candle_range_pct),
            )
        except Exception:
            return (False, None, None, None, None, None)

    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(_lookup, sym): sym for sym in unique}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result(timeout=20)
            except Exception:
                results[sym] = (False, None, None, None, None, None)
    return results


# ================================================================
# BIG PLAYERS STRATEGY ENDPOINTS
# ================================================================

@app.get("/api/strategies/bigplayers")
def get_big_players(budget: int = 100000, parts: int = 4):
    """
    Fetch Big Players strategy data.
    Uses the same base filters as before with Big Players columns:
    Symbol, Price, CHG%, Breakout, Support Price, MaxQty
    """
    if budget <= 0:
        raise HTTPException(status_code=400, detail="budget must be > 0")
    if parts < 1 or parts > 20:
        raise HTTPException(status_code=400, detail="parts must be between 1 and 20")

    try:
        # ─── Step 1: Fetch base stocks from TradingView ───
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

        raw_rows: list[dict] = []
        for symbol_row in (payload.get('data') or [])[:MAX_TV_STOCKS]:
            values = symbol_row.get('d') or []
            ticker = symbol_row.get('s')
            raw_rows.append(dict(zip(['ticker', *tv_columns], [ticker, *values])))

        df = pd.DataFrame(raw_rows)

        if df.empty:
            return {
                "strategy": "bigplayers",
                "name": "Big Players",
                "count": 0,
                "data": [],
                "columns": BIG_PLAYERS_COLUMNS,
                "message": "No stocks found matching the conditions"
            }

        # ─── Step 2: Apply Gap Filter (< 2%) ───
        df['gap'] = pd.to_numeric(df['gap'], errors='coerce')
        df = df[df['gap'].notna() & (abs(df['gap']) < GAP_THRESHOLD)]

        if df.empty:
            return {
                "strategy": "bigplayers",
                "name": "Big Players",
                "count": 0,
                "data": [],
                "columns": BIG_PLAYERS_COLUMNS,
                "message": f"No stocks with gap < {GAP_THRESHOLD}%"
            }

        # ─── Step 3: Get TODAY's opening candle data ───
        candidate_symbols = df['name'].dropna().astype(str).tolist()
        opening_candle_map = batch_opening_candle(candidate_symbols)

        # Compute EMA for qualification
        ema_map = compute_200_ema_batch(candidate_symbols)

        # Filter: small candle + close above 200 EMA + within 2%
        qualified_symbols = set()
        for s, candle_data in opening_candle_map.items():
            if not isinstance(candle_data, tuple) or not candle_data:
                continue
            is_small = candle_data[0]
            close915 = candle_data[4]
            ema = ema_map.get(s)

            # Only qualify if small candle AND close > 200 EMA within 2%
            if is_small and close915 is not None and ema is not None:
                pct_diff = abs((close915 - ema) / ema) * 100
                if close915 > ema and pct_diff <= 2.0:
                    qualified_symbols.add(s)
        df['ema'] = df['name'].map(ema_map)

        # Add high915/low915 for breakout calculation (only for qualified symbols)
        df['high915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None))[1]
        )
        df['low915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None))[3]
        )

        # ─── Step 4: Calculate MaxQty ───
        dhan_access_token = _broker_cred("access_token") or None
        df_qty = df.rename(columns={'name': 'Symbol', 'close': 'Price'})
        df_qty = calculate_max_quantity_column(
            df_qty,
            total_capital=budget,
            num_parts=parts,
            access_token=dhan_access_token,
        )
        df['MaxQty'] = df_qty['MaxQty'].values

        # ─── Step 5: Initialize Big Players Strategy ───
        bp_strategy = BigPlayersStrategy()

        # ─── Step 6: Transform for Big Players format ───
        result = []
        for _, row in df.iterrows():
            symbol = row['name']

            # Only include stocks that meet all conditions (small candle + EMA)
            if symbol not in qualified_symbols:
                continue

            # Prepare row for breakout calculation
            row_dict = {
                'Symbol': symbol,
                'Price': row['close'],
                'CHG%': row['change'],
                'high915': row.get('high915'),
                'low915': row.get('low915'),
                'ema': row.get('ema'),
            }

            breakout_status = bp_strategy.calculate_breakout_status(row_dict)
            support_price = bp_strategy.calculate_support_price(row_dict)

            result.append({
                "Symbol": symbol,
                "Price": round(row['close'], 2),
                "CHG%": round(row['change'], 2),
                "Breakout": breakout_status,
                "SupportPrice": support_price,
                "MaxQty": int(row.get("MaxQty", 0)),
            })

        return {
            "strategy": "bigplayers",
            "name": "Big Players",
            "count": len(result),
            "data": result,
            "columns": BIG_PLAYERS_COLUMNS,
            "conditions": {
                "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
                "gap": f"< {GAP_THRESHOLD}%",
                "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
                "exchange": "NSE",
                "small_candle": f"9:15 IST range <= {SMALL_CANDLE_THRESHOLD}%",
                "ema_condition": "9:15 close > 200 EMA & within 2%",
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategies/bigplayers/refresh")
def refresh_big_players(tickers: str = ""):
    """
    Lightweight refresh for Big Players strategy.
    Re-checks price, change, breakout status, and support price.
    Also re‑validates that the stock still has a small + red candle today.
    """
    try:
        symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if not symbols:
            return {"refreshed": []}

        # Get fresh price data from TradingView
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

        # Get today's opening candle data
        opening_candle_map = batch_opening_candle(symbols)
        ema_map = compute_200_ema_batch(symbols)

        # Build a set of symbols: small candle + close > 200 EMA within 2%
        valid_symbols = set()
        for s, candle_data in opening_candle_map.items():
            if not isinstance(candle_data, tuple) or not candle_data:
                continue
            is_small = candle_data[0]
            close915 = candle_data[4]
            ema = ema_map.get(s)
            if is_small and close915 is not None and ema is not None:
                pct_diff = abs((close915 - ema) / ema) * 100
                if close915 > ema and pct_diff <= 2.0:
                    valid_symbols.add(s)

        bp_strategy = BigPlayersStrategy()

        refreshed: list[dict] = []
        for symbol_row in (payload.get('data') or []):
            values = symbol_row.get('d') or []
            if len(values) < 3:
                continue
            name = values[0]
            if not name or name not in valid_symbols:
                continue

            close = pd.to_numeric(values[1], errors='coerce')
            change = pd.to_numeric(values[2], errors='coerce')

            if pd.isna(close) or pd.isna(change):
                continue

            high915 = opening_candle_map.get(name, (False, None))[1]
            low915 = opening_candle_map.get(name, (False, None, None))[3]
            ema = ema_map.get(name)

            row_dict = {
                'Symbol': name,
                'Price': close,
                'CHG%': change,
                'high915': high915,
                'low915': low915,
                'ema': ema,
            }

            breakout_status = bp_strategy.calculate_breakout_status(row_dict)
            support_price = bp_strategy.calculate_support_price(row_dict)

            refreshed.append({
                "Symbol": name,
                "Price": round(float(close), 2),
                "CHG%": round(float(change), 2),
                "Breakout": breakout_status,
                "SupportPrice": support_price,
            })

        return {"refreshed": refreshed}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/strategies/bigplayers/qty")
def recompute_bigplayers_qty(payload: dict):
    """
    Lightweight MaxQty recompute for Big Players strategy.
    """
    budget = payload.get("budget")
    parts = payload.get("parts")
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

    df_qty = calculate_max_quantity_column(
        pd.DataFrame(rows),
        total_capital=budget,
        num_parts=parts,
        access_token=_broker_cred("access_token") or None,
    )
    out = []
    for sym, q in zip(df_qty["Symbol"], df_qty["MaxQty"]):
        out.append({
            "Symbol": sym,
            "MaxQty": int(q) if pd.notna(q) else 0,
        })
    return {"data": out}


# ================================================================
# ROOT ENDPOINT
# ================================================================

@app.get("/api")
def root():
    return {
        "status": "ok",
        "message": "Big Players Strategy API",
        "conditions": {
            "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
            "gap": f"< {GAP_THRESHOLD}%",
            "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
            "exchange": "NSE",
            "first_candle": "small (≤1.5%), close > 200 EMA within 2%",
        },
        "strategies": ["bigplayers"]
    }


# ================================================================
# ORDER ENDPOINTS (unchanged)
# ================================================================

@app.post("/api/orders/place")
def place_order_endpoint(payload: dict):
    """Single-order placement for the manual Place-Order button."""
    symbol = (payload.get("symbol") or "").strip().upper()
    quantity = payload.get("quantity")
    transaction_type = (payload.get("transactionType") or "BUY").upper()
    product_type = (payload.get("productType") or "INTRADAY").upper()
    after_market_order = bool(payload.get("afterMarketOrder", False))
    amo_time = str(payload.get("amoTime") or "OPEN").upper()

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

    result = place_dhan_order(
        symbol=symbol,
        quantity=quantity,
        transaction_type=transaction_type,
        product_type=product_type,
        after_market_order=after_market_order,
        amo_time=amo_time,
    )
    if isinstance(result, dict) and not result.get("success") and result.get("symbol") == symbol:
        if isinstance(result.get("error"), str) and result["error"].startswith("Exception:"):
            raise HTTPException(status_code=502, detail=result["error"])
    return result


@app.post("/api/orders/place-batch")
def place_order_batch_endpoint(payload: dict):
    """Batch placement for Auto Buy. Hard cap: 5 orders."""
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

    def submit_one(order):
        return place_dhan_order(
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


# ================================================================
# HEALTH & STATIC FILES
# ================================================================

@app.get("/api/health")
def health():
    return {"status": "healthy"}


PROJECT_ROOT = Path(__file__).resolve().parent.parent

@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(PROJECT_ROOT / "index.html", media_type="text/html")

@app.get("/style.css", include_in_schema=False)
def stylesheet():
    return FileResponse(PROJECT_ROOT / "style.css", media_type="text/css")

app.mount("/js", StaticFiles(directory=PROJECT_ROOT / "js"), name="frontend-js")


# ================================================================
# BROKER ENDPOINTS
# ================================================================

@app.post("/api/broker/connect")
def broker_connect(payload: dict):
    broker = (payload.get("broker") or "").strip().lower()
    if broker != "dhan":
        raise HTTPException(
            status_code=400,
            detail=f"Broker {broker!r} not supported in this build. Pick Dhan.",
        )

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


@app.post("/api/broker/disconnect")
def broker_disconnect():
    clear_dhan_credentials()
    return {"ok": True, "connected": False}


@app.post("/api/broker/refresh-token")
def broker_refresh_token():
    return _dhan_renew()


@app.get("/api/broker/status")
def broker_status():
    cid = _broker_cred("client_id")
    tok = _broker_cred("access_token")
    issued_at = _broker_cred("token_issued_at")
    last_renewed_at = _broker_cred("token_last_renewed_at")
    expires_at = (
        float(issued_at) + DHAN_TOKEN_TTL_SECONDS if issued_at else None
    )
    seconds_until_expiry = (
        max(0, int(expires_at - time.time()))
        if expires_at else None
    )
    return {
        "connected": bool(tok) and bool(cid),
        "broker": _broker_cred("broker_name") or None,
        "client_id_masked":
            ("*" * (len(cid) - 4) + cid[-4:])
            if cid and len(cid) > 4 else None,
        "connected_at": _broker_cred("connected_at"),
        "token_issued_at": issued_at,
        "token_last_renewed_at": last_renewed_at,
        "expires_at": expires_at,
        "seconds_until_expiry": seconds_until_expiry,
        "auto_renew_in_seconds":
            max(0, int((expires_at - DHAN_AUTO_RENEW_LEAD_SECONDS) - time.time()))
            if expires_at else None,
    }


# ================================================================
# SPA FALLBACK
# ================================================================

@app.get("/{full_path:path}", include_in_schema=False)
def spa_fallback(full_path: str):
    if full_path.startswith(("api/", "js/", "style.css")):
        raise HTTPException(status_code=404, detail="Not Found")
    return FileResponse(PROJECT_ROOT / "index.html", media_type="text/html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)