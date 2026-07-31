"""
Shared constants and helper functions used by Advance ORB and Big Players strategies.
"""

from zoneinfo import ZoneInfo
import datetime
import time
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed

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
PRICE_MAX = 3000
GAP_THRESHOLD = 2.0
MARKET_CAP_MIN = 41_000_000_000  # 41 Billion INR
SMALL_CANDLE_THRESHOLD = 1.5

# ── 200-period EMA (auto-buy gate) ──
EMA_SPAN = 200
EMA_LOOKBACK_DAYS = 4

IST = ZoneInfo("Asia/Kolkata")
MAX_TV_STOCKS = 100
YFINANCE_WORKERS = 8


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
    """Return dict of {symbol: tick_data} with base-symbol aliases."""
    from broker.angel_ws import get_latest_ticks as angel_ws_ticks
    ticks = angel_ws_ticks()
    by_symbol = {}
    for token, data in ticks.items():
        sym = data.get("symbol", "") or ""
        if not sym:
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
        if base != sym:
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
