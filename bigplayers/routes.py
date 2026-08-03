"""
Big Players Strategy API Routes (FastAPI APIRouter)
"""

import hashlib
import asyncio
import pandas as pd
import re, requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from tradingview_screener import Query, col
from tradingview_screener.query import HEADERS as TV_HEADERS

from bigplayers.strategy import BigPlayersStrategy
from advance_orb.supabase_db import save_top5_strategy
from server.candle_tracker import candle_tracker
from advance_orb.common import (
    PRICE_MIN, PRICE_MAX, GAP_THRESHOLD, MARKET_CAP_MIN,
    SMALL_CANDLE_THRESHOLD, MAX_TV_STOCKS,
    batch_opening_candle, compute_200_ema_batch, _calc_qty_for_broker,
    _build_ticks_by_symbol, ws_auto_subscribe,
)
from broker.angel_margin_calculator import (
    is_connected as angel_is_connected,
    fill_margin_cache_async,
)
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
    "Entry Price",
    "SL",
    "MaxQty",
    "Risk ₹",
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
        # ─── Step 1: Build candidate list from watchlist + WebSocket ───
        # TV SCAN COMMENTED OUT for WS-only testing (Jul 31).
        # tv_query = (Query().select(...)...)
        #
        # Instead: use the 727-stock watchlist directly. Gap% is computed
        # from WebSocket LTP and cache's yesterday_close.
        # Always use latest_ticks (loaded from saved file at startup, plus
        # live WS ticks as they arrive).  The old guard
        # ``angel_is_connected() and angel_ws_connected()`` caused the
        # strategy to return empty when the WS was disconnected, even though
        # the saved ticks file has all 700+ stocks' last-known prices.
        ws_ticks = angel_ws_ticks()
        cache_data = candle_tracker._cache if hasattr(candle_tracker, '_cache') else {}

        raw_rows: list[dict] = []
        for sym in candle_tracker.token_by_symbol:
            tok = candle_tracker.token_by_symbol[sym]
            ws = ws_ticks.get(str(tok), {})
            cached = cache_data.get(sym, {})

            # Validate WS symbol — Angel One returns currency derivative data
            # for tokens shared across NSE equity and CDS segments.
            # If the symbol doesn't match {SYM}-EQ, fall back to yesterday's close.
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
            })

        df = pd.DataFrame(raw_rows) if raw_rows else pd.DataFrame()

        if df.empty:
            return {
                "strategy": "bigplayers",
                "name": "Big Players",
                "count": 0,
                "data": [],
                "columns": BIG_PLAYERS_COLUMNS,
                "message": "No stocks found matching the conditions"
            }

        candidate_symbols = df['name'].dropna().astype(str).tolist()

        # Get candle + EMA data
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

        # MaxQty from broker's real margin
        _calc_qty_for_broker(df, budget, parts)

        def _calc_entry_sl_risk(row):
            low = row.get('low915')
            high = row.get('high915')
            if pd.isna(low) or pd.isna(high) or high <= low:
                return 0, 0, 0
            entry_price = low + 0.65 * (high - low)
            sl_price = low
            risk_per_share = entry_price - sl_price
            if entry_price <= 0 or risk_per_share <= 0:
                return 0, 0, 0
            return round(entry_price, 2), round(sl_price, 2), round(risk_per_share, 2)

        # Evaluate Big Players strategy per stock
        bp_strategy = BigPlayersStrategy()
        result = []
        for _, row in df.iterrows():
            symbol = row['name']
            if symbol not in small_candle_symbols:
                continue

            candle = opening_candle_map.get(symbol)
            close915 = candle[4] if candle and len(candle) >= 5 else None
            ema = row.get('ema')
            if close915 is None or ema is None or pd.isna(ema):
                continue
            pct_diff = abs((close915 - ema) / ema) * 100
            if not (close915 > ema and pct_diff <= 2.0):
                continue

            today_low = candle[6] if candle and len(candle) >= 7 else None

            row_dict = {
                'Symbol': symbol,
                'Price': row['close'],
                'CHG%': row['change'],
                'high915': row.get('high915'),
                'low915': row.get('low915'),
                'ema': row.get('ema'),
                'todayLow': today_low,
            }
            entry_price, sl_price, risk_per_share = _calc_entry_sl_risk(row)
            breakout_status = bp_strategy.calculate_breakout_status(row_dict)
            support_price = bp_strategy.calculate_support_price(row_dict)
            broker_max_qty = int(row.get("MaxQty", 0))
            loss_if_hit = round(broker_max_qty * risk_per_share, 2) if risk_per_share > 0 else 0
            result.append({
                "Symbol": symbol,
                "Price": round(row['close'], 2),
                "CHG%": round(row['change'], 2),
                "Breakout": breakout_status,
                "SupportPrice": support_price,
                "EntryPrice": entry_price,
                "SL": sl_price,
                "MaxQty": broker_max_qty,
                "RiskRs": loss_if_hit,
                "TodayLow": round(today_low, 2) if today_low else None,
                "low915": round(row['low915'], 2) if pd.notna(row.get('low915')) else None,
                "high915": round(row['high915'], 2) if pd.notna(row.get('high915')) else None,
            })

        # Sort by CHG% descending (highest gainers first)
        result.sort(key=lambda x: x['CHG%'], reverse=True)

        # Save top 5 to Supabase for historical tracking
        try:
            save_top5_strategy("bigplayers", result[:5])
        except Exception:
            pass  # never break the screener over a DB hiccup

        # Kick off background margin fill for ALL candidate stocks (~800)
        # so the cache is fully populated for the weekly refresh cycle.
        try:
            fill_margin_cache_async(
                df['name'].dropna().astype(str).tolist(),
                df['close'].dropna().astype(float).tolist(),
            )
        except Exception:
            pass

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


@router.get("/api/strategies/bigplayers/refresh")
def refresh_big_players(tickers: str = ""):
    """Lightweight refresh for Big Players strategy."""
    try:
        symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if not symbols:
            return {"refreshed": []}

        opening_candle_map = batch_opening_candle(symbols)
        ema_map = compute_200_ema_batch(symbols)
        bp_strategy = BigPlayersStrategy()

        # TV SCAN COMMENTED OUT for WS-only testing (Jul 31).
        # tv_query = (Query().select(...)...)
        # Instead: pull data directly from latest_ticks (saved + live).
        ws_ticks = {}
        raw = angel_ws_ticks()
        for token, d in raw.items():
            sym = d.get("symbol", "")
            if sym:
                ws_ticks[sym] = d
                base = sym.split("-")[0]
                if base != sym:
                    ws_ticks[base] = d

        refreshed: list[dict] = []
        for name in symbols:
            ws = ws_ticks.get(name)
            if not ws:
                continue
            close = ws.get("ltp")
            change = ws.get("change_pct", 0)
            if close is None or float(close) <= 0:
                continue

            # EMA rule: 9:15 close > 200 EMA within 2%
            candle = opening_candle_map.get(name)
            close915 = candle[4] if candle and len(candle) >= 5 else None
            ema = ema_map.get(name)
            if close915 is None or ema is None:
                continue
            pct_diff = abs((close915 - ema) / ema) * 100
            if not (close915 > ema and pct_diff <= 2.0):
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
                'todayLow': today_low,
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
                is_conn = angel_is_connected() and angel_ws_connected()
                payload = _json.dumps({
                    "connected": is_conn,
                    "ticks": _build_ticks_by_symbol(),
                })
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
