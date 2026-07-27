from pathlib import Path
from zoneinfo import ZoneInfo

import os
import requests
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
from broker.quantity_calculator import calculate_max_quantity_column
from broker.dhan_orders import place_dhan_order

app = FastAPI(
    title="TradeAlgo Pro - Advance ORB",
    description="Fetches NSE stocks with price 200-3000, gap < 2%, market cap > 41B",
    version="1.0.0"
)

# CORS - Allow frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── HARDCODED CONDITIONS ───
PRICE_MIN = 200
PRICE_MAX = 3000
GAP_THRESHOLD = 2.0
MARKET_CAP_MIN = 41_000_000_000  # 41 Billion INR
SMALL_CANDLE_THRESHOLD = 1.5
# ── 200-period EMA filter ──
# We use Yahoo Finance 5-minute candles for the past
# `EMA_LOOKBACK_DAYS` trading days to derive a 200-period EMA per
# symbol. The screener keeps only rows whose
#       |price − EMA| / EMA × 100 ≤ EMA_DISTANCE_PCT (%)
# Default 3 % keeps price close enough to the trend to be a "real"
# breakout and drops far-stretched / far-broken-down names.
EMA_SPAN = 200
EMA_LOOKBACK_DAYS = 4
EMA_DISTANCE_PCT = 3.0
IST = ZoneInfo("Asia/Kolkata")
MAX_TV_STOCKS = 200
YFINANCE_WORKERS = 8
ADVANCE_ORB_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "GAP%",
    "Volume",
    "RELVOL",
    "Sector",
    "Small Candle",
    "200 EMA",
    "MaxQty",
]


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

    # yfinance can return a MultiIndex even for one ticker.
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
        local_index = local_index.tz_localize(IST)
    else:
        local_index = local_index.tz_convert(IST)
    candles = candles.copy()
    candles.index = local_index

    opening_candles = candles[
        (candles.index.hour == 9) & (candles.index.minute == 15)
    ]
    if opening_candles.empty:
        return False

    candle = opening_candles.iloc[-1]
    high = pd.to_numeric(candle["High"], errors="coerce")
    low = pd.to_numeric(candle["Low"], errors="coerce")
    if pd.isna(high) or pd.isna(low) or low <= 0:
        return False

    candle_range = (high - low) / low * 100
    return candle_range <= SMALL_CANDLE_THRESHOLD


def compute_200_ema(symbol: str) -> float | None:
    """Compute the 200-period EMA for `symbol` using Yahoo Finance's
    5-min closing prices over the past `EMA_LOOKBACK_DAYS` trading
    days. Returns the EMA value (float) or None if too little data
    is available (<200 candles) or the symbol is missing on yfinance.
    """
    if not symbol:
        return None
    try:
        ticker = f"{symbol}.NS"
        df = yf.download(
            ticker,
            period=f"{EMA_LOOKBACK_DAYS}d",
            interval="5m",
            progress=False,
            auto_adjust=True,
        )
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(ticker, axis=1, level=-1)
            except Exception:
                try:
                    df = df.xs(ticker, axis=1, level=0)
                except Exception:
                    return None
        if "Close" not in df.columns:
            return None
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if closes.empty or len(closes) < EMA_SPAN:
            return None
        ema = closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
        return float(ema) if pd.notna(ema) else None
    except Exception:
        return None


def compute_200_ema_batch(symbols: list[str]) -> dict[str, float | None]:
    """Run compute_200_ema across many symbols in parallel (reusing the
    same `YFINANCE_WORKERS` thread pool).

    Returns: {symbol: ema_value_or_None}.
    """
    results: dict = {}
    unique = [s for s in {s for s in symbols if s}]
    if not unique:
        return results
    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(compute_200_ema, sym): sym for sym in unique}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                ema = fut.result(timeout=20)
                results[sym] = ema
            except Exception:
                results[sym] = None
    return results


def batch_opening_candle(symbols: list[str]) -> dict:
    """For each symbol, fetch yfinance 5-min candles ONLY ONCE and
    return both pieces of information the screener needs from the
    9:15 IST opening 5-min candle:
        * is the candle "small" (range ≤ SMALL_CANDLE_THRESHOLD %)
        * the candle's HIGH price (used as `high915` for the auto-buy
          9:15 high-price-band filter on the frontend)
    Returns: {symbol: (is_small: bool, high915: float | None)}.
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
                return (False, None)
            if isinstance(candles.columns, pd.MultiIndex):
                try:
                    candles = candles.xs(ticker, axis=1, level=-1)
                except (KeyError, IndexError):
                    try:
                        candles = candles.xs(ticker, axis=1, level=0)
                    except (KeyError, IndexError):
                        return (False, None)
            if "High" not in candles or "Low" not in candles:
                return (False, None)
            local_index = pd.DatetimeIndex(candles.index)
            if local_index.tz is None:
                local_index = local_index.tz_localize(IST)
            else:
                local_index = local_index.tz_convert(IST)
            candles = candles.copy()
            candles.index = local_index
            opening = candles[
                (candles.index.hour == 9) & (candles.index.minute == 15)
            ]
            if opening.empty:
                return (False, None)
            candle = opening.iloc[-1]
            high = pd.to_numeric(candle["High"], errors="coerce")
            low = pd.to_numeric(candle["Low"], errors="coerce")
            open915 = pd.to_numeric(candle["Open"], errors="coerce")
            if pd.isna(high) or pd.isna(low) or low <= 0:
                return (False, None, None)
            is_small = bool(((high - low) / low * 100) <= SMALL_CANDLE_THRESHOLD)
            open_val = float(open915) if pd.notna(open915) else None
            return (is_small, float(high), open_val)
        except Exception:
            return (False, None, None)

    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(_lookup, sym): sym for sym in unique}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                results[sym] = fut.result(timeout=20)
            except Exception:
                results[sym] = (False, None, None)
    return results


def filter_small_opening_candles(symbols: list[str]) -> set[str]:
    """Yahoo Finance 9:15 IST candle check, executed across many symbols in parallel.

    Each unique ticker is fetched with `yf.download` inside a thread pool. Any
    failure (delisted ticker, missing data, rate limit, exception) is treated
    as "not a small opening candle" so the symbol is excluded from results.
    """
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


@app.get("/api")
def root():
    return {
        "status": "ok",
        "message": "Advance ORB Strategy API",
        "conditions": {
            "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
            "gap": f"< {GAP_THRESHOLD}%",
            "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
            "exchange": "NSE"
        }
    }

@app.get("/api/strategies/advanceorb")
def get_advance_orb(budget: int = 100000, parts: int = 4):
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
        # Run the Yahoo Finance candle check across every candidate in parallel,
        # then keep only rows whose ticker is in the matching set.
        candidate_symbols = df['name'].dropna().astype(str).tolist()

        # First fetch the 9:15 IST 5-min candle per symbol — the same
        # single yfinance pull gives us BOTH:
        #   * `is_small`  (small-candle gate below)
        #   * `open915`   (today's OPEN price, used by the 200-EMA
        #                  distance filter below)
        # We fetch this BEFORE the EMA filter so `df['open915']` is
        # defined when the EMA filter consumes it.
        opening_candle_map = batch_opening_candle(candidate_symbols)
        small_candle_symbols = {
            s for s, t in opening_candle_map.items()
            if isinstance(t, tuple) and t and t[0]
        }
        # "open915" = today's OPEN price (= the first 5-min candle's Open).
        # Used for the 200-EMA distance check instead of the live close,
        # so a stock that opens within the band keeps its row even when
        # the live price subsequently moves beyond 3% of EMA.
        df['open915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None))[2]
        )

        # ── Step 3a: 200-period EMA distance filter ──
        # Compute EMA per candidate in parallel via yfinance 5-min candles.
        # Drop rows where the 200-EMA is missing OR
        # |today_open − EMA| / EMA × 100 > EMA_DISTANCE_PCT.
        # Also surface `ema` and `change_pct` as numeric columns so the
        # frontend can render the 200 EMA cell and sort numerically.
        ema_map = compute_200_ema_batch(candidate_symbols)
        df = df.copy()
        df['ema'] = df['name'].map(ema_map)
        df['change_pct'] = pd.to_numeric(df['change'], errors='coerce')
        df = df[df['ema'].notna()]
        # IMPORTANT: distance check uses TODAY'S OPEN (9:15 candle Open),
        # not the live `close`. A stock that opened within ±3% of the
        # 200-EMA keeps its place in the table even when intraday price
        # later drifts further away from EMA.
        df = df[df['open915'].notna()]
        ema_pct = (df['open915'] - df['ema']).abs() / df['ema'] * 100.0
        df = df[ema_pct <= EMA_DISTANCE_PCT]

        # Calculate Max Quantities via Dhan (see broker/quantity_calculator.py).
        # quantity_calculator expects Symbol/Price cols; df has name/close.
        # Token from DHAN_ACCESS_TOKEN env var (Replit Secrets).
        dhan_access_token = os.environ.get('DHAN_ACCESS_TOKEN', '').strip() or None
        df_qty = df.rename(columns={'name': 'Symbol', 'close': 'Price'})
        df_qty = calculate_max_quantity_column(
            df_qty,
            total_capital=budget,
            num_parts=parts,
            access_token=dhan_access_token,
        )
        df['MaxQty'] = df_qty['MaxQty'].values

        result = []
        for _, row in df.iterrows():
            symbol = row['name']
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

            result.append({
                "Symbol": symbol,
                "Price": round(row['close'], 2),
                "CHG%": round(row['change'], 2),
                "GAP%": round(row['gap'], 2),
                "Volume": volume_str,
                "RELVOL": relvol_str,
                "Sector": row.get('sector', 'Unknown'),
                "Small Candle": "✓",
                "ema": round(float(row["ema"]), 2),
                "high915": (
                    round(float(opening_candle_map[symbol][1]), 2)
                    if opening_candle_map.get(symbol)
                    and opening_candle_map[symbol][1] is not None
                    else None
                ),
                "open915": (
                    round(float(opening_candle_map[symbol][2]), 2)
                    if opening_candle_map.get(symbol)
                    and opening_candle_map[symbol][2] is not None
                    else None
                ),
                "MaxQty": int(row.get("MaxQty", 0)),
            })

        return {
            "strategy": "advanceorb",
            "name": "Advance ORB",
            "count": len(result),
            "data": result,
            "columns": ADVANCE_ORB_COLUMNS,
            "conditions": {
                "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
                "gap": f"< {GAP_THRESHOLD}%",
                "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
                "exchange": "NSE",
                "small_candle": f"9:15 IST range <= {SMALL_CANDLE_THRESHOLD}%",
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

    result = place_dhan_order(
        symbol=symbol,
        quantity=quantity,
        transaction_type=transaction_type,
        product_type=product_type,
        after_market_order=after_market_order,
        amo_time=amo_time,
    )
    # Mirror place_dhan_order.success to HTTP status — caller can read .error on 4xx/5xx.
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


@app.get("/api/health")
def health():
    return {"status": "healthy"}


@app.get("/api/strategies/advanceorb/refresh")
def refresh_advance_orb(tickers: str = ""):
    """Lightweight refresh of price/volume/change for a fixed list of tickers.

    Skips the Yahoo candle check so it stays around ~0.5–1.5 s even for
    ~150 symbols. Returns refreshed values per symbol; symbols TradingView
    cannot resolve are simply omitted from the response.
    """
    try:
        symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if not symbols:
            return {"refreshed": []}

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

        refreshed: list[dict] = []
        for symbol_row in (payload.get('data') or []):
            values = symbol_row.get('d') or []
            if len(values) < 5:
                continue
            name = values[0]
            if not name:
                continue
            close = pd.to_numeric(values[1], errors='coerce')
            change = pd.to_numeric(values[2], errors='coerce')
            vol = pd.to_numeric(values[3], errors='coerce')
            relvol = pd.to_numeric(values[4], errors='coerce')

            if pd.notna(vol) and vol >= 1_000_000:
                volume_str = f"{vol/1_000_000:.1f}M"
            elif pd.notna(vol) and vol >= 1_000:
                volume_str = f"{vol/1_000:.1f}K"
            elif pd.notna(vol):
                volume_str = str(int(vol))
            else:
                volume_str = "0"
            relvol_str = f"{relvol:.2f}x" if pd.notna(relvol) else "0x"

            refreshed.append({
                "Symbol": name,
                "Price": round(float(close), 2) if pd.notna(close) else None,
                "CHG%": round(float(change), 2) if pd.notna(change) else None,
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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
