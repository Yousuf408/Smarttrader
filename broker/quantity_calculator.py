# ─────────────────────────────────────────────────────────────────────────────
# QUANTITY CALCULATOR MODULE (Standalone)
#
# Calculates max quantity per stock using DhanHQ's live Margin Calculator API.
# ─────────────────────────────────────────────────────────────────────────────

import pandas as pd
import requests
import io
import math
import pyotp
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─── DHAN CREDENTIALS & API URLs ───
DHAN_CLIENT_ID = "1102302753"
DHAN_PIN = "786786"
DHAN_TOTP_SECRET = "THWBRO5KI5N7ACJUNY7W3JUDKL4M2LML"

# API URLs
DHAN_MARGIN_CALCULATOR_URL = "https://api.dhan.co/v2/margincalculator"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_TOKEN_GENERATE_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_FUND_LIMIT_URL = "https://api.dhan.co/v2/fundlimit"

# ─── PROXY SETTINGS ───
DHAN_PROXY_HOST = "151.242.178.149"
DHAN_PROXY_PORT = "50100"
DHAN_PROXY_USERNAME = "yousufshaikh420"
DHAN_PROXY_PASSWORD = "cVTbJi6VVA"
DHAN_PROXY_URL = f"http://{DHAN_PROXY_USERNAME}:{DHAN_PROXY_PASSWORD}@{DHAN_PROXY_HOST}:{DHAN_PROXY_PORT}"
DHAN_PROXIES = {"http": DHAN_PROXY_URL, "https": DHAN_PROXY_URL}

# ─── IN-MEMORY CACHE ───
_margin_cache = {}
_token_cache = None
_token_expiry = None
_security_map_cache = None
_security_map_cache_time = None


# ─────────────────────────────────────────────────────────────────────────────
# TOKEN MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

def get_access_token(force_refresh=False):
    """Get valid Dhan access token via TOTP."""
    global _token_cache, _token_expiry

    now = datetime.now()

    # Return cached token if valid
    if not force_refresh and _token_cache and _token_expiry and now < _token_expiry:
        return _token_cache

    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        response = requests.post(
            DHAN_TOKEN_GENERATE_URL,
            params={"dhanClientId": DHAN_CLIENT_ID, "pin": DHAN_PIN, "totp": totp},
            proxies=DHAN_PROXIES,
            timeout=10,
        )

        if response.status_code != 200:
            return None

        data = response.json()
        token = data.get("accessToken")
        if not token:
            return None

        _token_cache = token
        _token_expiry = now + timedelta(hours=23)
        return token

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SECURITY ID MAP (Cached for 24 hours)
# ─────────────────────────────────────────────────────────────────────────────

def get_security_id_map():
    """Load Dhan instrument master and map symbol → security_id."""
    global _security_map_cache, _security_map_cache_time

    # Check cache
    if _security_map_cache and _security_map_cache_time:
        if datetime.now() - _security_map_cache_time < timedelta(hours=24):
            return _security_map_cache

    try:
        response = requests.get(DHAN_SCRIP_MASTER_URL, timeout=30)
        if response.status_code != 200:
            return {}

        df = pd.read_csv(io.StringIO(response.text), low_memory=False)

        # Find columns
        symbol_col = next((c for c in df.columns if c.upper() == "UNDERLYING_SYMBOL"), None)
        sec_id_col = next((c for c in df.columns if c.upper() == "SECURITY_ID"), None)

        if not symbol_col or not sec_id_col:
            return {}

        # Filter to NSE Equity
        df = df[df["SEGMENT"].astype(str).str.upper() == "E"]
        df = df[df["SEM_EXM_EXCH_ID"].astype(str).str.upper() == "NSE"]

        security_map = {}
        for _, row in df.iterrows():
            symbol = str(row[symbol_col]).strip().upper()
            sec_id = str(row[sec_id_col]).strip()
            if symbol and sec_id:
                security_map[symbol] = sec_id

        _security_map_cache = security_map
        _security_map_cache_time = datetime.now()
        return security_map

    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# MARGIN CALCULATOR
# ─────────────────────────────────────────────────────────────────────────────

def get_margin_per_share(security_id, price, product_type="INTRADAY"):
    """Get margin required for 1 share via Dhan API."""
    token = get_access_token()
    if not token:
        return None

    try:
        payload = {
            "dhanClientId": str(DHAN_CLIENT_ID),
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "quantity": 1,
            "productType": product_type,
            "securityId": str(security_id),
            "price": float(price),
            "triggerPrice": 0,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": token,
        }

        response = requests.post(
            DHAN_MARGIN_CALCULATOR_URL,
            json=payload,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10,
        )

        if response.status_code == 401:
            token = get_access_token(force_refresh=True)
            if not token:
                return None
            headers["access-token"] = token
            response = requests.post(
                DHAN_MARGIN_CALCULATOR_URL,
                json=payload,
                headers=headers,
                proxies=DHAN_PROXIES,
                timeout=10,
            )

        if response.status_code != 200:
            return None

        data = response.json()
        margin = data.get("totalMargin")
        return float(margin) if margin is not None else None

    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# BATCH FETCH (Parallel)
# ─────────────────────────────────────────────────────────────────────────────

def get_margins_batch_parallel(symbols, prices, max_workers=10):
    """Fetch margins for multiple stocks in parallel."""
    results = {}
    security_map = get_security_id_map()

    def fetch_one(symbol, price):
        sec_id = security_map.get(symbol.upper())
        if not sec_id:
            return symbol, None
        margin = get_margin_per_share(sec_id, price)
        return symbol, margin

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_one, symbol, price): symbol
            for symbol, price in zip(symbols, prices)
            if symbol and price > 0
        }

        for future in as_completed(futures):
            try:
                symbol, margin = future.result()
                if margin is not None and margin > 0:
                    results[symbol] = margin
            except Exception:
                continue

    return results


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────

def calculate_max_quantity_column(df, total_capital, num_parts=4, max_stocks=20):
    """
    Calculate MaxQty for stocks in DataFrame.

    Args:
        df: DataFrame with 'Symbol' and 'Price' columns
        total_capital: Total trading capital
        num_parts: Number of parts to split capital into
        max_stocks: Max stocks to calculate for (default 20)

    Returns:
        Series with MaxQty values
    """
    if df.empty or total_capital <= 0:
        return pd.Series([0] * len(df), index=df.index)

    part_capital = total_capital / num_parts

    # Take only first max_stocks
    df_subset = df.head(max_stocks)
    symbols = [str(s).strip().upper() for s in df_subset["Symbol"]]
    prices = [float(p) for p in df_subset["Price"]]

    # Get margins in parallel
    margins = get_margins_batch_parallel(symbols, prices)

    # Calculate MaxQty
    max_qty_list = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip().upper()
        price = row.get("Price", 0)

        margin = margins.get(symbol)
        if margin and margin > 0 and price > 0:
            max_qty = math.floor(part_capital / margin)
            max_qty_list.append(max(max_qty, 0))
        else:
            max_qty_list.append(0)

    return pd.Series(max_qty_list, index=df.index)
