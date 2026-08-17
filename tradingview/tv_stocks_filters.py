"""
TradingView stocks scanner + filters (separate shared module).

This file owns the TradingView NSE universe screen that the Advance ORB (and
other strategies) use to build their scan list.  It returns the SAME rows (
and columns) as the original ``advance_orb.common.fetch_tradingview_stocks``,
so callers only need to change the import path — behaviour is unchanged.

Column mapping (order of ``columns`` in the scan request):
    index  column
    -----  ------------------------------------
      0    name
      1    description
      2    close
      3    change
      4    gap
      5    volume
      6    relative_volume_10d_calc
      7    market_cap_basic
      8    sector
      9    open
     10    high
     11    low
     12    change_from_open
"""
from __future__ import annotations

import threading
import time
import logging

logger = logging.getLogger("tradingview.tv_stocks_filters")

# ─── Screen conditions (Advance ORB's stock universe) ─────────────
PRICE_MIN = 200
PRICE_MAX = 4000          # 200 to 4000 INR
GAP_THRESHOLD = 2.0       # exclude |gap| >= 2%
MARKET_CAP_MIN = 41_000_000_000  # 41 Billion INR

# ── TradingView scanner endpoint ─────────────────────────────────
TV_SCAN_URL = "https://scanner.tradingview.com/india/scan"
TV_SCAN_TTL = 600         # 10 minutes — don't hammer the free endpoint
_tv_scan_lock = threading.Lock()
_tv_scan_cache: list[dict] = []
_tv_scan_cached_at = 0.0


def fetch_tradingview_stocks() -> list[dict]:
    """NSE universe straight from TradingView (not the local watchlist).

    Screen: type=stock AND exchange=NSE AND
            close 200-4000 INR AND market_cap_basic > 41B INR.
    Every matching stock is returned regardless of its % change from the
    day's open (down-drifting names are included too).

    Returns all matching rows as
        [{name, close, change, gap, volume, relative_volume,
          market_cap_basic, sector, open, high, low,
          change_from_open}, ...]

    WARNING: open/high/low here are the FULL-DAY bar (the scan's base row
    is the daily snapshot), so they must NEVER be used as the 9:15 opening
    candle.  The true 9:15 values come from CandleTracker slot 0 or the
    authenticated TradingView chart feed instead.

    Results are cached for TV_SCAN_TTL seconds.  On network / API failure
    returns the stale cache if any, else an empty list.
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
            "change_from_open",
        ],
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"},
            {"left": "exchange", "operation": "equal", "right": "NSE"},
            {"left": "close", "operation": "greater", "right": PRICE_MIN},
            {"left": "close", "operation": "less", "right": PRICE_MAX},
            {"left": "market_cap_basic", "operation": "greater", "right": MARKET_CAP_MIN},
        ],
        "sort": {"sortBy": "market_cap_basic", "sortOrder": "desc"},
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
    except Exception as e:  # noqa: BLE001
        logger.warning("tv-scan: TradingView scan failed: %s", e)
        with _tv_scan_lock:
            return _tv_scan_cache  # stale data beats an empty tab

    rows: list[dict] = []
    for item in body.get("data", []):
        d = item.get("d") or []
        if len(d) < 12:
            continue
        name = str(d[0] or "").strip().upper()
        close = d[2]
        if not name or not isinstance(close, (int, float)) or close <= 0:
            continue
        if not (PRICE_MIN < close <= PRICE_MAX):
            continue
        gap = float(d[4]) if isinstance(d[4], (int, float)) else 0.0
        if abs(gap) >= GAP_THRESHOLD:
            continue
        change_from_open = float(d[12]) if isinstance(d[12], (int, float)) else 0.0
        rows.append({
            "name": name,
            "close": float(close),
            "change": float(d[3]) if isinstance(d[3], (int, float)) else 0.0,
            "gap": gap,
            "volume": float(d[5]) if isinstance(d[5], (int, float)) else 0,
            "relative_volume": float(d[6]) if isinstance(d[6], (int, float)) else 0.0,
            "market_cap_basic": float(d[7]) if isinstance(d[7], (int, float)) else 0,
            "sector": str(d[8]) if d[8] else "N/A",
            "change_from_open": change_from_open,
            "open": float(d[9]) if len(d) > 9 and isinstance(d[9], (int, float)) else None,
            "high": float(d[10]) if len(d) > 10 and isinstance(d[10], (int, float)) else None,
            "low": float(d[11]) if len(d) > 11 and isinstance(d[11], (int, float)) else None,
        })
    rows.sort(key=lambda r: -r["market_cap_basic"])

    with _tv_scan_lock:
        _tv_scan_cache = rows
        _tv_scan_cached_at = time.time()
    logger.info(
        "tv-scan: %d NSE stocks (200-4000 INR, mcap > 41B, all change%% included)",
        len(rows),
    )
    return rows


# Re-export from the old location so existing callers keep working until they
# migrate.  This keeps the working logic intact during the refactor.
__all__ = [
    "fetch_tradingview_stocks",
    "TV_SCAN_URL",
    "TV_SCAN_TTL",
    "PRICE_MIN",
    "PRICE_MAX",
    "GAP_THRESHOLD",
    "MARKET_CAP_MIN",
]
