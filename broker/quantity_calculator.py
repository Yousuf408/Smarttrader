# ================================================================
# QUANTITY CALCULATOR - Dhan API Integration
# Streamlit code removed, FastAPI compatible
# ================================================================

import time
import requests
import pandas as pd
import math
import io
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================================================================
# DHAN CREDENTIALS — runtime store (populated by app.py via
# /api/broker/connect after the user enters them in the popup).
# Other modules downstream (broker/dhan_orders, advance_orb/app.py)
# still import the legacy module-level names (DHAN_CLIENT_ID,
# DHAN_TOTP_SECRET, DHAN_MANUAL_ACCESS_TOKEN, DHAN_ACCESS_TOKEN,
# DHAN_PIN). To keep those imports working without code edits in
# the downstream files, each of those names is now a `_Cred` proxy
# object whose str(...) returns the current value from the store.
# ================================================================
_DHAN_CREDS = {
    "client_id": "",
    "pin": "",
    "totp_secret": "",
    "access_token": "",
    "broker_name": None,   # "dhan" once connected
    "connected_at": None,  # time.time() once connected
}


class _Cred:
    """Module-level proxy that resolves to the current value of a key in
    the runtime `_DHAN_CREDS` dict. Re-evaluated on every str() / bool()
    access so a /api/broker/connect done mid-session shows up on the
    next request without re-importing anything.
    """
    __slots__ = ("_key",)

    def __init__(self, key):
        self._key = key

    def __str__(self):
        return _DHAN_CREDS.get(self._key) or ""

    def __bool__(self):
        return bool(_DHAN_CREDS.get(self._key))

    def __repr__(self):
        return f"_Cred({self._key!r}={str(self)!r})"


DHAN_CLIENT_ID = _Cred("client_id")
DHAN_PIN = _Cred("pin")
DHAN_TOTP_SECRET = _Cred("totp_secret")
DHAN_MANUAL_ACCESS_TOKEN = _Cred("access_token")
DHAN_ACCESS_TOKEN = _Cred("access_token")


def _cred(key):
    """Public reader for app.py endpoints. Returns "" if unset."""
    return _DHAN_CREDS.get(key) or ""


def set_dhan_credentials(client_id, pin, totp_secret, broker_name="dhan"):
    """Store user-supplied Dhan credentials. Access-token mint happens
    in app.py (which holds the requests/timeout logic) and lands via
    set_dhan_access_token below."""
    _DHAN_CREDS["client_id"] = str(client_id or "").strip()
    _DHAN_CREDS["pin"] = str(pin or "").strip()
    _DHAN_CREDS["totp_secret"] = str(totp_secret or "").strip()
    _DHAN_CREDS["broker_name"] = broker_name
    _DHAN_CREDS["connected_at"] = time.time()


def set_dhan_access_token(token):
    """Cache the freshly-minted access token. Empty string clears it.
    Also stamps token_issued_at so the auto-renew loop knows when to
    re-mint before Dhan's daily expiry."""
    tok = str(token or "").strip()
    _DHAN_CREDS["access_token"] = tok
    now = time.time()
    if tok:
        # First setup = both stamps land at "issued"; subsequent calls
        # land at "renewed".
        if not _DHAN_CREDS.get("token_issued_at"):
            _DHAN_CREDS["token_issued_at"] = now
        _DHAN_CREDS["token_last_renewed_at"] = now
    else:
        _DHAN_CREDS["token_issued_at"] = None
        _DHAN_CREDS["token_last_renewed_at"] = None


def clear_dhan_credentials():
    """Drop the runtime credentials so subsequent /margincalculator
    calls fail closed. Used by /api/broker/disconnect."""
    _DHAN_CREDS["client_id"] = ""
    _DHAN_CREDS["pin"] = ""
    _DHAN_CREDS["totp_secret"] = ""
    _DHAN_CREDS["access_token"] = ""
    _DHAN_CREDS["broker_name"] = None
    _DHAN_CREDS["connected_at"] = None
    _DHAN_CREDS["token_issued_at"] = None
    _DHAN_CREDS["token_last_renewed_at"] = None


# ================================================================
# DAILY-AUTO-RENEW CONSTANTS
# ================================================================
# Dhan's access token expires roughly 24 h after issue. We renew
# ~AUTO_RENEW_LEAD_SECONDS before that wall so neither the user
# nor the screener ever see a stale-token window.
DHAN_TOKEN_TTL_SECONDS = 24 * 3600
DHAN_AUTO_RENEW_LEAD_SECONDS = 60 * 60  # renew 1 h before expiry


def renew_dhan_access_token():
    """Mint a fresh Dhan access token via /RenewToken (server-to-server,
    no PIN or TOTP needed because we're already authenticated by the
    existing JWT + client_id pair). Works against just-expired tokens
    too — Dhan honours the renew path for the whole rolling window.
    Returns dict (ok / connected / detail / status_code). Updates
    _DHAN_CREDS["access_token"] on success."""
    cid = _DHAN_CREDS.get("client_id") or ""
    cur = _DHAN_CREDS.get("access_token") or ""
    if not cid:
        return {"ok": False, "connected": False, "detail":
                "not connected (no client_id)", "status_code": 0}
    if not cur:
        return {"ok": False, "connected": False, "detail":
                "not connected (no access_token to renew from)", "status_code": 0}
    try:
        # Mirror what the official DhanHQ-py SDK does:
        #   GET https://api.dhan.co/RenewToken
        #   headers: {"access-token": <current>, "dhanClientId": <cid>}
        # Proxies are reused so /RenewToken respects the same
        # IP-whitelist contract as /v2/margincalculator.
        r = requests.get(
            "https://api.dhan.co/RenewToken",
            headers={"access-token": cur, "dhanClientId": cid},
            proxies=DHAN_PROXIES,
            timeout=10,
        )
    except Exception as e:
        return {"ok": False, "connected": False, "detail":
                f"network error: {e}", "status_code": 0}
    if r.status_code == 200 and r.text:
        try:
            data = r.json()
        except Exception:
            data = {}
        new_token = (data.get("accessToken") or data.get("access_token")
                     or data.get("token") or "").strip()
        if new_token:
            set_dhan_access_token(new_token)
            return {"ok": True, "connected": True, "detail": "renewed",
                    "status_code": 200}
    return {"ok": False, "connected": bool(cur), "detail":
            (r.text or "")[:200], "status_code": r.status_code}

# ================================================================
# PROXY CONFIGURATION — Used for brokers that require static-IP
# whitelisting (Angel One always, Dhan for order placement).
# ================================================================
DHAN_PROXY_HOST = "151.242.178.149"
DHAN_PROXY_PORT = "50100"
DHAN_PROXY_USERNAME = "yousufshaikh420"
DHAN_PROXY_PASSWORD = "cVTbJi6VVA"
DHAN_PROXY_URL = f"http://{DHAN_PROXY_USERNAME}:{DHAN_PROXY_PASSWORD}@{DHAN_PROXY_HOST}:{DHAN_PROXY_PORT}"
DHAN_PROXIES = {"http": DHAN_PROXY_URL, "https": DHAN_PROXY_URL}

# ================================================================
# DHAN API URLS
# ================================================================
DHAN_MARGIN_CALCULATOR_URL = "https://api.dhan.co/v2/margincalculator"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_TOKEN_GENERATE_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_FUND_LIMIT_URL = "https://api.dhan.co/v2/fundlimit"

# ================================================================
# ACCESS TOKEN — see lines 20-50 above for the runtime store. The
# module-level DHAN_ACCESS_TOKEN name is the `_Cred("access_token")`
# proxy reassigned at line 58; do NOT introduce any further
# `DHAN_ACCESS_TOKEN = ...` here — that would overwrite the proxy
# with a static string at import time and silently short-circuit
# every /v2/margincalculator call to "0" (which is exactly what
# tripped the screener when the popup-based flow first went live).
# `get_margin_per_share` reads the proxy directly; refresh works
# by mutating `_DHAN_CREDS["access_token"]` and the proxy resolves
# the new value on every str() call.
# ================================================================

# Per-symbol margin cache. Survives across screener refreshes so that
# when stocks shuffle positions in top-N, only newly-arrived symbols
# trigger a real Dhan call. Stable symbols bypass the round-trip,
# which keeps us well under Dhan's /margincalculator rate limit.
MARGIN_CACHE = {}
MARGIN_CACHE_TTL_SECONDS = 15 * 60  # 15 minutes

# Cache for security ID map
SECURITY_ID_CACHE = {}
MASTER_CSV_CACHE = None


# ================================================================
# LOAD INSTRUMENT MASTER
# ================================================================
def load_instrument_master():
    """Load Dhan instrument master CSV - cache for performance"""
    global MASTER_CSV_CACHE

    if MASTER_CSV_CACHE is not None:
        return MASTER_CSV_CACHE

    try:
        response = requests.get(DHAN_SCRIP_MASTER_URL, timeout=30)
        if response.status_code != 200:
            print(f"❌ Failed to load master CSV: {response.status_code}")
            return pd.DataFrame()

        MASTER_CSV_CACHE = pd.read_csv(io.StringIO(response.text), low_memory=False)
        print(f"✅ Loaded {len(MASTER_CSV_CACHE)} instruments from Dhan master")
        return MASTER_CSV_CACHE
    except Exception as e:
        print(f"❌ Error loading master CSV: {e}")
        return pd.DataFrame()


# ================================================================
# GET SECURITY ID
# ================================================================
def get_security_id(symbol):
    """Get Security ID from Dhan master CSV"""
    if not symbol:
        return None

    # Check cache first
    if symbol.upper() in SECURITY_ID_CACHE:
        return SECURITY_ID_CACHE[symbol.upper()]

    try:
        df = load_instrument_master()
        if df.empty:
            return None

        # Find UNDERLYING_SYMBOL column
        symbol_col = next(
            (c for c in df.columns if "UNDERLYING_SYMBOL" in c.upper()), None
        )
        if not symbol_col:
            symbol_col = next((c for c in df.columns if "SYMBOL" in c.upper()), None)

        # Find SECURITY_ID column
        sec_id_col = next((c for c in df.columns if "SECURITY_ID" in c.upper()), None)

        if not symbol_col or not sec_id_col:
            print(f"❌ Required columns not found in master CSV")
            return None

        # Filter for NSE Equity.
        # Dhan changed its master CSV: SEM_EXM_EXCH_ID is now EXCH_ID,
        # and SEGMENT carries the segment code (E = Equity for cash equities).
        exch_col = next((c for c in df.columns if c.upper() == "EXCH_ID"), None)
        seg_col = next((c for c in df.columns if c.upper() == "SEGMENT"), None)
        if not exch_col or not seg_col:
            print("❌ EXCH_ID / SEGMENT columns not found in master CSV")
            return None
        nse_df = df[
            (df[exch_col].astype(str).str.upper() == "NSE") &
            (df[seg_col].astype(str).str.upper() == "E")
        ]
        row = nse_df[nse_df[symbol_col].str.upper() == symbol.upper()]

        if row.empty:
            print(f"⚠️ Symbol {symbol} not found in master CSV")
            return None

        sec_id = str(row[sec_id_col].values[0])
        SECURITY_ID_CACHE[symbol.upper()] = sec_id
        return sec_id
    except Exception as e:
        print(f"❌ Error getting security ID for {symbol}: {e}")
        return None


# ================================================================
# GET MARGIN PER SHARE
# ================================================================
def get_margin_per_share(security_id, price, access_token=None):
    """Call Dhan margin calculator API for single stock"""
    token = access_token or DHAN_ACCESS_TOKEN

    # Margin cache hit returns immediately. Keyed on (security_id,
    # rounded price) with a 15 min TTL. When stocks shuffle in the
    # top-N screener, only newly-arrived symbols trigger a real Dhan
    # call; stable symbols reuse the cached margin. We never cache
    # 429 / error returns so retries are not masked.
    cache_key = (str(security_id), round(float(price), 2))
    cached = MARGIN_CACHE.get(cache_key)
    if cached is not None and (time.time() - cached[1]) < MARGIN_CACHE_TTL_SECONDS:
        return cached[0]

    if not token or token == "your_dhan_access_token_here":
        print("⚠️ Access token not set")
        return 0

    try:
        payload = {
            "dhanClientId": str(DHAN_CLIENT_ID),
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "quantity": 1,
            "productType": "INTRADAY",
            "securityId": str(security_id),
            "price": float(price),
            "triggerPrice": 0,
        }

        headers = {
            "Content-Type": "application/json",
            "access-token": token,
        }

        response = requests.post(
            DHAN_MARGIN_CALCULATOR_URL,
            json=payload,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            margin = float(data.get("totalMargin", 0))
            if margin > 0:
                MARGIN_CACHE[cache_key] = (margin, time.time())
            return margin
        else:
            print(f"⚠️ Dhan API error: {response.status_code}")
            return 0
    except Exception as e:
        print(f"❌ Error calculating margin for security {security_id}: {e}")
        return 0


# ================================================================
# CALCULATE SINGLE STOCK QTY
# ================================================================
def calculate_qty(symbol, price, total_capital, num_parts=4, access_token=None):
    """Calculate max quantity for single stock"""
    if not symbol or not price or total_capital <= 0:
        return 0

    try:
        # Get security ID
        sec_id = get_security_id(symbol)
        if not sec_id:
            print(f"⚠️ Could not get security ID for {symbol}")
            return 0

        # Get margin per share
        margin = get_margin_per_share(sec_id, price, access_token)
        if margin <= 0:
            print(f"⚠️ Invalid margin for {symbol}: {margin}")
            return 0

        # Split capital into parts (4 parts = 25% per trade)
        part_capital = total_capital / num_parts

        # Calculate qty: capital / margin per share
        qty = math.floor(part_capital / margin)

        return max(qty, 0)
    except Exception as e:
        print(f"❌ Error calculating qty for {symbol}: {e}")
        return 0


# ================================================================
# CALCULATE MULTIPLE STOCKS (PARALLEL)
# ================================================================
def calculate_max_quantities(
    symbols, prices, total_capital=100000, num_parts=4, access_token=None
):
    """Calculate quantities for multiple stocks in parallel"""
    if not symbols or not prices or len(symbols) != len(prices):
        return {}

    results = {}

    def fetch_qty(symbol, price):
        qty = calculate_qty(symbol, price, total_capital, num_parts, access_token)
        return symbol, qty

    try:
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {
                executor.submit(fetch_qty, sym, price): sym
                for sym, price in zip(symbols, prices)
            }

            for future in as_completed(futures):
                try:
                    symbol, qty = future.result()
                    results[symbol] = qty
                except Exception as e:
                    symbol = futures[future]
                    print(f"❌ Error processing {symbol}: {e}")
                    results[symbol] = 0
    except Exception as e:
        print(f"❌ Error in parallel processing: {e}")
        for symbol in symbols:
            results[symbol] = 0

    return results


# ================================================================
# ADD MAXQTY COLUMN TO DATAFRAME
# ================================================================
def calculate_max_quantity_column(
    df, total_capital=100000, num_parts=4, access_token=None
):
    """Add MaxQty column to dataframe"""
    try:
        if df.empty:
            df["MaxQty"] = 0
            return df

        # Extract symbols and prices
        symbol_col = next((c for c in df.columns if c.upper() == "SYMBOL"), None)
        price_col = next((c for c in df.columns if c.upper() == "PRICE"), None)

        if not symbol_col or not price_col:
            print("⚠️ Symbol or Price column not found")
            df["MaxQty"] = 0
            return df

        symbols = df[symbol_col].astype(str).tolist()
        prices = pd.to_numeric(df[price_col], errors="coerce").tolist()

        # Calculate quantities in parallel
        qty_map = calculate_max_quantities(
            symbols, prices, total_capital, num_parts, access_token
        )

        # Add to dataframe
        df["MaxQty"] = df[symbol_col].map(qty_map).fillna(0).astype(int)

        return df
    except Exception as e:
        print(f"❌ Error adding MaxQty column: {e}")
        df["MaxQty"] = 0
        return df


# ================================================================
# TEST FUNCTION
# ================================================================
def test_qty_calculation():
    """Test qty calculation"""
    print("\n📊 Testing Qty Calculator...")

    # Test data
    test_symbols = ["RELIANCE", "TCS", "INFY"]
    test_prices = [2856.40, 3920.00, 1545.00]

    print(f"Testing with symbols: {test_symbols}")
    print(f"Testing with prices: {test_prices}")

    # Calculate
    results = calculate_max_quantities(test_symbols, test_prices, total_capital=100000)

    print("\n✅ Results:")
    for symbol, qty in results.items():
        print(f"  {symbol}: Qty = {qty}")

    return results


if __name__ == "__main__":
    test_qty_calculation()

# ================================================================
# EAGER INIT: preload master CSV once at process start
# ================================================================
# Without this, each of the 8 worker threads independently races to
# download the 218k-row scrip master CSV on the first screener call,
# burning ~2-3 s × 8 of duplicate network work per refresh. Eager
# loading at module-import time means the data is in memory before
# uvicorn starts accepting connections, so every worker reads from
# one canonical copy.
try:
    load_instrument_master()
    print("✅ Master CSV preloaded at startup")
except Exception as _exc:
    print(f"⚠️  Eager master CSV preload failed: {_exc}")

