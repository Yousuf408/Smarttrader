"""
Big Players Strategy API Routes (FastAPI APIRouter)
"""

import hashlib
import asyncio
import pandas as pd
import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from tradingview_screener import Query, col
from tradingview_screener.query import HEADERS as TV_HEADERS

from bigplayers.strategy import BigPlayersStrategy
from advance_orb.common import (
    PRICE_MIN, PRICE_MAX, GAP_THRESHOLD, MARKET_CAP_MIN,
    SMALL_CANDLE_THRESHOLD, MAX_TV_STOCKS,
    batch_opening_candle, compute_200_ema_batch, _calc_qty_for_broker,
    _build_ticks_by_symbol, ws_auto_subscribe,
)
from broker.angel_margin_calculator import is_connected as angel_is_connected
from broker.angel_ws import (
    is_ws_connected as angel_ws_connected,
    get_latest_ticks as angel_ws_ticks,
)

router = APIRouter(tags=["bigplayers"])

BIG_PLAYERS_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "Breakout",
    "Support Price",
    "9:15 High",
    "9:15 Low",
    "MaxQty",
]


@router.get("/api/strategies/bigplayers")
def get_big_players(budget: int = 100000, parts: int = 4):
    """
    Fetch Big Players strategy data.
    Uses the same stocks as Advance ORB with Big Players columns:
    Symbol, Price, CHG%, Breakout, Support Price, MaxQty
    """
    if budget <= 0:
        raise HTTPException(status_code=400, detail="budget must be > 0")
    if parts < 1 or parts > 20:
        raise HTTPException(status_code=400, detail="parts must be between 1 and 20")

    try:
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
                "message": "No stocks found"
            }

        # Gap filter
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

        # Get candle + EMA data
        candidate_symbols = df['name'].dropna().astype(str).tolist()
        opening_candle_map = batch_opening_candle(candidate_symbols)
        small_candle_symbols = {
            s for s, t in opening_candle_map.items()
            if isinstance(t, tuple) and t and t[0]
        }
        ema_map = compute_200_ema_batch(candidate_symbols)
        df['ema'] = df['name'].map(ema_map)
        df['high915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None))[1]
        )
        df['low915'] = df['name'].map(
            lambda s: opening_candle_map.get(s, (False, None, None, None))[3]
        )

        # MaxQty
        _calc_qty_for_broker(df, budget, parts)

        # Evaluate Big Players strategy per stock
        bp_strategy = BigPlayersStrategy()
        result = []
        for _, row in df.iterrows():
            symbol = row['name']
            if symbol not in small_candle_symbols:
                continue

            candle = opening_candle_map.get(symbol)
            close915 = candle[4] if candle and len(candle) >= 5 else None
            open915 = candle[2] if candle and len(candle) >= 3 else None
            if close915 is None or open915 is None or close915 >= open915:
                continue

            today_low = candle[6] if candle and len(candle) >= 7 else None

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
                "TodayLow": round(today_low, 2) if today_low else None,
                "low915": round(row['low915'], 2) if pd.notna(row.get('low915')) else None,
                "high915": round(row['high915'], 2) if pd.notna(row.get('high915')) else None,
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
                "red_candle": "9:15 candle close < open (must be red)",
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/strategies/bigplayers/refresh")
def refresh_big_players(tickers: str = ""):
    """Lightweight refresh for Big Players strategy."""
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
        response = requests.post(tv_query.url, json=body, headers=TV_HEADERS, timeout=20)
        response.raise_for_status()
        payload = response.json()

        opening_candle_map = batch_opening_candle(symbols)
        ema_map = compute_200_ema_batch(symbols)
        bp_strategy = BigPlayersStrategy()

        # WebSocket tick data overlay
        ws_ticks = {}
        if angel_is_connected() and angel_ws_connected():
            raw = angel_ws_ticks()
            for token, d in raw.items():
                sym = d.get("symbol", "")
                if sym:
                    ws_ticks[sym] = d
                    base = sym.split("-")[0]
                    if base != sym:
                        ws_ticks[base] = d
            ws_auto_subscribe(symbols)

        refreshed: list[dict] = []
        for symbol_row in (payload.get('data') or []):
            values = symbol_row.get('d') or []
            if len(values) < 3:
                continue
            name = values[0]
            if not name:
                continue
            close = pd.to_numeric(values[1], errors='coerce')
            change = pd.to_numeric(values[2], errors='coerce')

            # Overlay WS tick data
            ws = ws_ticks.get(name)
            if ws:
                ws_ltp = ws.get("ltp")
                if ws_ltp is not None:
                    close = float(ws_ltp)
                ws_chg = ws.get("change_pct")
                if ws_chg is not None:
                    change = round(float(ws_chg), 2)

            if pd.isna(close) or pd.isna(change):
                continue

            # Red-candle rule: 9:15 close < open
            candle = opening_candle_map.get(name)
            close915 = candle[4] if candle and len(candle) >= 5 else None
            open915 = candle[2] if candle and len(candle) >= 3 else None
            if close915 is None or open915 is None or close915 >= open915:
                continue

            high915 = candle[1] if candle and len(candle) >= 2 else None
            low915 = candle[3] if candle and len(candle) >= 4 else None
            today_low = candle[6] if candle and len(candle) >= 7 else None
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
                "TodayLow": round(today_low, 2) if today_low else None,
                "low915": round(low915, 2) if low915 is not None else None,
                "high915": round(high915, 2) if high915 is not None else None,
            })

        return {"refreshed": refreshed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/strategies/bigplayers/qty")
def recompute_bigplayers_qty(payload: dict):
    """Lightweight MaxQty recompute for Big Players strategy."""
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
    df_in = pd.DataFrame(rows)
    _calc_qty_for_broker(df_in, budget, parts)
    out = []
    for sym, q in zip(df_in["Symbol"], df_in["MaxQty"]):
        out.append({
            "Symbol": sym,
            "MaxQty": int(q) if pd.notna(q) else 0,
        })
    return {"data": out}


@router.get("/api/market/bigplayers-ticks/stream")
async def stream_bigplayers_ticks():
    """Server-Sent Events endpoint for Big Players — dedicated stream."""
    import json as _json

    async def _bp_generate():
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
                    yield ": heartbeat\n\n"
            except Exception:
                yield f"data: {_json.dumps({'connected': False, 'ticks': {}})}\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(
        _bp_generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
        },
    )
