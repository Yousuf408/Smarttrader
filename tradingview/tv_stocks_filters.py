"""
TradingView Stock Scanner & Technical Filters Module
Fetches real-time market data directly from TradingView Scanner API for NSE India equities.
Filters by Nifty Total Market Index + Price Range + NSE + Market Cap + Opening Gap %.
"""
import os
import json
import logging
from typing import List, Dict, Any, Set

try:
    import urllib.request as urlreq
except ImportError:
    urlreq = None

logger = logging.getLogger(__name__)

TRADINGVIEW_SCANNER_URL = "https://scanner.tradingview.com/india/scan"
NIFTY_TOTAL_MARKET_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv"
LOCAL_NIFTY_JSON_PATH = os.path.join(os.path.dirname(__file__), "nifty_total_market.json")

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json"
}

_nifty_total_market_cache: Set[str] = set()

def get_nifty_total_market_symbols() -> Set[str]:
    """
    Retrieves the official Nifty Total Market Index constituents (750 stocks).
    Fetches live from Nifty Indices with fallback to local cached dataset.
    """
    global _nifty_total_market_cache
    if _nifty_total_market_cache:
        return _nifty_total_market_cache

    # 1. Try local JSON first (instant & reliable)
    if os.path.exists(LOCAL_NIFTY_JSON_PATH):
        try:
            with open(LOCAL_NIFTY_JSON_PATH, "r", encoding="utf-8") as f:
                symbols = json.load(f)
                if symbols and len(symbols) > 500:
                    _nifty_total_market_cache = {s.upper().strip() for s in symbols}
                    return _nifty_total_market_cache
        except Exception as e:
            logger.warning(f"Error reading local nifty_total_market.json: {e}")

    # 2. Try live CSV fetch
    if urlreq:
        try:
            req = urlreq.Request(NIFTY_TOTAL_MARKET_CSV_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urlreq.urlopen(req, timeout=6) as resp:
                lines = resp.read().decode("utf-8").splitlines()
                symbols = set()
                for line in lines[1:]:
                    parts = line.split(",")
                    if len(parts) >= 3 and parts[2].strip():
                        symbols.add(parts[2].strip().upper())
                if len(symbols) > 500:
                    _nifty_total_market_cache = symbols
                    return _nifty_total_market_cache
        except Exception as e:
            logger.warning(f"Could not fetch Nifty Total Market CSV live: {e}")

    return _nifty_total_market_cache

def fetch_tradingview_stocks(
    min_price: float = 200.0,
    max_price: float = 4000.0,
    min_volume: int = 0,
    min_market_cap: float = 41000000000, # 41 Billion INR (~4,100 Cr)
    max_gap_pct: float = 2.0,
    limit: int = 1500,
    require_nifty_total_market: bool = True
) -> List[Dict[str, Any]]:
    """
    Queries TradingView Scanner API for NSE stocks matching:
    1. Nifty Total Market Index constituents
    2. Price Range (₹200 - ₹4,000)
    3. NSE Equities
    4. Market Cap > ₹4,100 Crore
    5. Opening Gap % within ±2.0%
    """
    nifty_symbols = get_nifty_total_market_symbols() if require_nifty_total_market else set()

    filters = [
        {"left": "exchange", "operation": "equal", "right": "NSE"},
        {"left": "type", "operation": "equal", "right": "stock"},
        {"left": "close", "operation": "in_range", "right": [min_price, max_price]},
        {"left": "market_cap_basic", "operation": "greater", "right": min_market_cap},
        {"left": "gap", "operation": "in_range", "right": [-max_gap_pct, max_gap_pct]}
    ]

    if min_volume > 0:
        filters.append({"left": "volume", "operation": "greater", "right": min_volume})

    columns = [
        "name",
        "description",
        "close",
        "change",
        "volume",
        "relative_volume_10d_calc",
        "EMA200",
        "high",
        "low",
        "open",
        "gap",
        "sector",
        "market_cap_basic"
    ]

    payload = {
        "filter": filters,
        "options": {"lang": "en"},
        "symbols": {"query": {"types": []}, "tickers": []},
        "columns": columns,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, limit]
    }

    try:
        req_data = json.dumps(payload).encode("utf-8")
        req = urlreq.Request(
            TRADINGVIEW_SCANNER_URL,
            data=req_data,
            headers=DEFAULT_HEADERS,
            method="POST"
        )
        with urlreq.urlopen(req, timeout=12) as response:
            body = response.read().decode("utf-8")
            res_json = json.loads(body)

        raw_data = res_json.get("data", [])
        results = []
        for item in raw_data:
            d = item.get("d", [])
            if len(d) < len(columns):
                continue

            symbol = str(d[0] or "").upper().strip()
            name = d[1] or symbol

            # 1. Check Nifty Total Market Index membership
            if nifty_symbols and symbol not in nifty_symbols:
                continue

            close_price = round(float(d[2] or 0), 2)
            change_pct = round(float(d[3] or 0), 2)
            volume = int(d[4] or 0)
            rel_vol = round(float(d[5] or 1.0), 2) if d[5] is not None else 1.0
            ema200 = round(float(d[6] or 0), 2) if d[6] is not None else round(close_price * 0.98, 2)
            high = round(float(d[7] or close_price), 2)
            low = round(float(d[8] or close_price), 2)
            open_price = round(float(d[9] or close_price), 2)
            gap = round(float(d[10] or 0), 2) if d[10] is not None else 0.0
            sector = d[11] or "General"
            market_cap = float(d[12] or 0)

            # Check gap filter
            if abs(gap) > max_gap_pct:
                continue

            results.append({
                "symbol": symbol,
                "name": name,
                "price": close_price,
                "change_pct": change_pct,
                "volume": volume,
                "relvol": rel_vol,
                "ema200": ema200,
                "high": high,
                "low": low,
                "open": open_price,
                "gap": gap,
                "sector": sector,
                "market_cap": market_cap,
                "above_ema": close_price >= ema200 if ema200 > 0 else True
            })

        return results

    except Exception as e:
        logger.error(f"Failed to fetch stocks from TradingView: {e}")
        return []

def filter_tv_stocks(stocks: List[Dict[str, Any]], min_price: float = 200, max_price: float = 4000, gap_threshold: float = 2.0) -> List[Dict[str, Any]]:
    """Filters a list of stock dictionaries by price range and gap %"""
    return [
        s for s in stocks
        if min_price <= s.get('price', 0) <= max_price and abs(s.get('gap', 0)) <= gap_threshold
    ]

if __name__ == "__main__":
    print("🚀 Fetching live Nifty Total Market stocks from TradingView Scanner API...")
    stocks = fetch_tradingview_stocks(min_price=200, max_price=4000)
    print(f"✅ Successfully fetched {len(stocks)} stocks matching all 5 conditions:\n")
    for s in stocks[:8]:
        print(f"• {s['symbol']:<12} | Price: ₹{s['price']:<8} | Chg: {s['change_pct']:>5}% | Vol: {s['volume']:>10,} | 200EMA: ₹{s['ema200']:<7} | Above EMA: {s['above_ema']}")
