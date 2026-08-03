# ================================================================
# ANGEL ONE MARGIN CALCULATOR - Single File Solution
# WITH PROXY SUPPORT FOR IP WHITELISTING
# AND FEED TOKEN SUPPORT FOR WEBSOCKET
# ================================================================

import time
import requests
import json
import math
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed

# ================================================================
# PROXY CONFIGURATION (for IP whitelisting - required for orders)
# ================================================================
ANGEL_PROXY_HOST = "151.242.178.149"
ANGEL_PROXY_PORT = "50100"
ANGEL_PROXY_USERNAME = "yousufshaikh420"
ANGEL_PROXY_PASSWORD = "cVTbJi6VVA"
ANGEL_PROXY_URL = f"http://{ANGEL_PROXY_USERNAME}:{ANGEL_PROXY_PASSWORD}@{ANGEL_PROXY_HOST}:{ANGEL_PROXY_PORT}"
ANGEL_PROXIES = {"http": ANGEL_PROXY_URL, "https": ANGEL_PROXY_URL}

print(f"✅ Angel One Proxy configured: {ANGEL_PROXY_HOST}:{ANGEL_PROXY_PORT}")

# ================================================================
# CREDENTIALS STORE (In-memory only)
# ================================================================
_CREDS = {
    "api_key": "",
    "client_id": "",
    "password": "",
    "totp_secret": "",
    "access_token": "",
    "refresh_token": "",
    "feed_token": "",  # ← ADDED FOR WEBSOCKET
    "token_issued_at": 0,  # epoch seconds — for auto-renew tracking
}

# Persistent SmartConnect SDK instance (created once on auth, reused for
# margin/order calls so the SDK's custom headers — X-UserType, X-SourceID,
# X-ClientPublicIP, X-MACAddress etc. — are sent on every request and
# the WAF doesn't block them).
_SMART_API = None

# ================================================================
# API CONSTANTS
# ================================================================
ANGEL_BASE_URL = "https://apiconnect.angelbroking.com"
ANGEL_LOGIN_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/user/v1/loginByClientID"
ANGEL_MARGIN_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/margin/v1/batch"
ANGEL_SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# ================================================================
# CACHE
# ================================================================
MARGIN_CACHE = {}
MARGIN_CACHE_TTL = 15 * 60  # 15 minutes
SECURITY_CACHE = {}
MASTER_CACHE = None
MASTER_CACHE_TIME = 0
MASTER_TTL = 24 * 60 * 60  # 24 hours

# ================================================================
# CORE FUNCTIONS
# ================================================================

def set_credentials(api_key, client_id, password, totp_secret=None):
    """Set Angel One credentials."""
    _CREDS["api_key"] = str(api_key or "").strip()
    _CREDS["client_id"] = str(client_id or "").strip()
    _CREDS["password"] = str(password or "").strip()
    _CREDS["totp_secret"] = str(totp_secret or "").strip() if totp_secret else ""
    print(f"✅ Credentials set for client: {client_id}")

def get_access_token():
    """Get current access token."""
    return _CREDS.get("access_token", "")

def is_connected():
    """Check if connected to Angel One."""
    return bool(_CREDS.get("access_token"))

def set_feed_token(feed_token):
    """Store feed token for WebSocket."""
    _CREDS["feed_token"] = str(feed_token or "").strip()
    print(f"✅ Feed token stored")

def get_feed_token():
    """Get feed token for WebSocket."""
    return _CREDS.get("feed_token", "")

def _make_smart_connect():
    """Create a SmartConnect instance with the stored credentials."""
    from SmartApi import SmartConnect
    sc = SmartConnect(api_key=_CREDS.get("api_key"))
    # Proxy for IP whitelisting — routes order placement through a
    # registered static IP.  Proxy server is available again as of Aug 2026.
    sc.proxies = ANGEL_PROXIES
    return sc

def authenticate():
    """Authenticate with Angel One using the SmartConnect SDK.

    Uses the official SmartApi SDK (SmartConnect.generateSession) which
    sends the correct TLS fingerprint and headers that Angel One's WAF
    expects — raw requests.post() triggers a WAF block even when the IP
    is properly whitelisted.
    """
    api_key    = _CREDS.get("api_key")
    client_id  = _CREDS.get("client_id")
    password   = _CREDS.get("password")
    totp_secret = _CREDS.get("totp_secret")

    if not api_key or not client_id or not password:
        return {"ok": False, "error": "Missing credentials. Call set_credentials() first."}

    print(f"🔐 Authenticating Angel One for: {client_id}")

    try:
        smart_api = _make_smart_connect()

        # Generate TOTP
        totp_code = None
        if totp_secret:
            try:
                import pyotp
                totp_code = pyotp.TOTP(totp_secret).now()
                print("✅ TOTP generated")
            except Exception as e:
                print(f"⚠️ TOTP error (proceeding without TOTP): {e}")

        # Authenticate via the official SDK
        data = smart_api.generateSession(client_id, password, totp_code or "")

        if data is None:
            return {"ok": False, "error": "SmartConnect returned None — likely a network or proxy error."}

        if data.get("status") == False:
            error_msg = data.get("message") or data.get("error") or "Authentication failed"
            return {"ok": False, "error": error_msg}

        jwt_token_raw = data["data"].get("jwtToken", "")
        refresh_token = data["data"].get("refreshToken", "")

        if not jwt_token_raw:
            return {"ok": False, "error": "No jwtToken in SDK response"}

        # The API response sometimes includes "Bearer " as a prefix on the
        # jwtToken. The SDK's _request method does `"Bearer {}".format(token)`
        # when building the Authorization header, so we must strip any
        # existing prefix to avoid a double "Bearer Bearer eyJ..." header.
        jwt_token = jwt_token_raw
        if jwt_token.startswith("Bearer "):
            jwt_token = jwt_token[7:]

        # Get feed token from the SDK object
        try:
            feed_token = smart_api.getfeedToken()
        except Exception:
            feed_token = data["data"].get("feedToken") or data["data"].get("feed_token", "")

        # Store credentials
        _CREDS["access_token"]   = jwt_token
        _CREDS["refresh_token"]  = refresh_token
        _CREDS["token_issued_at"] = str(time.time())  # for auto-renew tracking
        if feed_token:
            _CREDS["feed_token"] = str(feed_token)
            print(f"✅ Feed token received and stored")

        # Store the SDK instance for margin/order calls
        global _SMART_API
        smart_api.setAccessToken(jwt_token)
        smart_api.setUserId(client_id)
        _SMART_API = smart_api

        print("✅ Authentication successful!")
        return {
            "ok": True,
            "access_token":  jwt_token,
            "refresh_token": refresh_token,
            "feed_token":    feed_token or "",
        }

    except Exception as e:
        err_msg = str(e)
        # Catch known error patterns
        if "Request Rejected" in err_msg:
            return {
                "ok": False,
                "error": (
                    "Angel One WAF blocked the request even through the SDK. "
                    "Verify that your API Key is active and the proxy IP "
                    "151.242.178.149 is whitelisted in your Angel One portal."
                ),
            }
        return {"ok": False, "error": err_msg}

def disconnect():
    """Clear all credentials."""
    global _SMART_API
    for key in _CREDS:
        _CREDS[key] = ""
    _SMART_API = None
    print("🔌 Disconnected from Angel One")
    return {"ok": True}

# ================================================================
# INSTRUMENT MASTER
# ================================================================

def load_master():
    """Load Angel One scrip master."""
    global MASTER_CACHE, MASTER_CACHE_TIME

    if MASTER_CACHE is not None and (time.time() - MASTER_CACHE_TIME) < MASTER_TTL:
        return MASTER_CACHE

    try:
        # Try direct first (scrip master is a CDN file, not behind the WAF).
        # Fall back to proxy if direct fails (e.g. regional block).
        try:
            response = requests.get(ANGEL_SCRIP_MASTER_URL, timeout=30)
            if response.status_code != 200 or "Request Rejected" in (response.text or ""):
                raise ConnectionError("Direct failed")
        except Exception:
            response = requests.get(
                ANGEL_SCRIP_MASTER_URL,
                proxies=ANGEL_PROXIES,
                timeout=30,
            )

        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                df = pd.DataFrame(data)
            elif isinstance(data, dict) and "data" in data:
                df = pd.DataFrame(data["data"])
            else:
                return None

            MASTER_CACHE = df
            MASTER_CACHE_TIME = time.time()
            print(f"✅ Loaded {len(df)} instruments")
            return df
        else:
            print(f"❌ Failed to load master: HTTP {response.status_code}")
            return None
    except Exception as e:
        print(f"❌ Error loading master: {e}")
        return None

def resolve_symbol_token(symbol, exchange="NSE"):
    """Look up the Angel One trading symbol and token for a symbol/exchange.

    Returns (symbol, token) or (None, None) if not found.  The returned
    symbol is the *exact* name from the scrip master — important because
    the Angel One order API rejects a mismatch between tradingsymbol and
    symboltoken (e.g. ``RELIANCE-EQ`` token ``2885`` ≠ ``RELIANCE``).
    """
    cache_key = f"{exchange}:{symbol.upper()}"
    cached = SECURITY_CACHE.get(cache_key)
    if cached and isinstance(cached, tuple):
        return cached

    df = load_master()
    if df is None:
        return None, None

    symbol_col = next((c for c in df.columns if 'symbol' in c.lower()), None)
    token_col  = next((c for c in df.columns if 'token'  in c.lower()), None)
    exch_col   = next((c for c in df.columns if 'exch'   in c.lower()), None)

    if not symbol_col or not token_col:
        print("❌ Required columns not found in master")
        return None, None

    # Try exact match first, then symbol-EQ (Angel One's internal
    # equity format — e.g. RELIANCE-EQ with token 2885).
    candidates = [symbol.upper(), f"{symbol.upper()}-EQ"]
    for sym in candidates:
        mask = df[symbol_col].astype(str).str.upper() == sym
        if exch_col:
            mask &= df[exch_col].astype(str).str.upper() == exchange.upper()
        matched = df[mask]
        if not matched.empty:
            row = matched.iloc[0]
            found_sym = str(row[symbol_col])
            found_token = str(row[token_col])
            SECURITY_CACHE[cache_key] = (found_sym, found_token)
            print(f"✅ Resolved {symbol} ({exchange}) -> symbol={found_sym}, token={found_token}")
            return found_sym, found_token

    print(f"⚠️ Symbol {symbol} not found in master")
    return None, None


def is_stock_tradable(symbol, exchange="NSE"):
    """Check if a stock is CAS-enabled (tradable) in Angel One.

    The Angel One scrip master has an ``is_cas_enabled`` field.
    When ``False``, the exchange has flagged the stock as cautionary
    and Angel One's order API will reject it with:
    "The order cannot be processed as the token is categorised under
    cautionary listings by the exchange."

    Returns:
        tuple: (tradable: bool, reason: str | None)
               ``(True, None)`` if tradable;
               ``(False, "Cautionary listing")`` if blocked.
    """
    df = load_master()
    if df is None:
        return True, None  # can't check — let order API decide

    symbol_col = next((c for c in df.columns if 'symbol' in c.lower()), None)
    exch_col   = next((c for c in df.columns if 'exch' in c.lower()), None)
    cas_col    = next((c for c in df.columns if 'cas_enabled' in c.lower()), None)

    if not symbol_col or cas_col is None:
        return True, None

    candidates = [symbol.upper(), f"{symbol.upper()}-EQ"]
    for sym in candidates:
        mask = df[symbol_col].astype(str).str.upper() == sym
        if exch_col:
            mask &= df[exch_col].astype(str).str.upper() == exchange.upper()
        matched = df[mask]
        if not matched.empty:
            cas = matched.iloc[0].get(cas_col)
            if cas is False or str(cas).lower() == "false":
                return False, "Cautionary listing"
            return True, None

    return True, None  # not found in master — let order API decide


def get_token(symbol, exchange="NSE"):
    """Get security token for symbol (kept for backward compatibility)."""
    sym, token = resolve_symbol_token(symbol, exchange)
    return token

# ================================================================
# MARGIN CALCULATION
# ================================================================

def get_margin(symbol, price, quantity=1, exchange="NSE", product_type="INTRADAY"):
    """Get margin for a single stock."""
    # Check positive cache
    cache_key = (symbol, round(float(price), 2))
    cached = MARGIN_CACHE.get(cache_key)
    if cached and (time.time() - cached[1]) < MARGIN_CACHE_TTL:
        return cached[0]
    # Check negative cache — rate-limited stocks skip retry for 30 s
    neg_key = (symbol, "NEG")
    neg = MARGIN_CACHE.get(neg_key)
    if neg and (time.time() - neg[1]) < 30:
        return 0

    # Check connection
    if not is_connected():
        print("⚠️ Not connected to Angel One")
        return 0

    # Get token
    token = get_token(symbol, exchange)
    if not token:
        print(f"⚠️ Token not found for {symbol}")
        return 0

    try:
        global _SMART_API
        if _SMART_API is None:
            print("⚠️ SDK instance not available, re-authenticating...")
            auth_result = authenticate()
            if not auth_result.get("ok"):
                return 0

        # Angel One's margin API expects camelCase field names:
        #   product_type → productType,  transaction_type → tradeType,
        #   ordertype    → orderType,    quantity         → qty
        payload = {
            "positions": [{
                "symbol": symbol,
                "token": token,
                "exchange": exchange,
                "productType": product_type,
                "tradeType": "BUY",
                "orderType": "MARKET",
                "price": float(price),
                "qty": quantity
            }]
        }

        # Timeout so a hung API never stalls the entire page
        import concurrent.futures as _cf
        with _cf.ThreadPoolExecutor(max_workers=1) as _pool:
            _fut = _pool.submit(_SMART_API.getMarginApi, payload)
            result = _fut.result(timeout=15)

        if result and result.get("status") and result.get("data"):
            margin = result["data"].get("totalMarginRequired", 0)
            if margin > 0:
                MARGIN_CACHE[cache_key] = (margin, time.time())
            return margin

        # Detect rate-limit / access-denied — negative-cache so we skip
        err_text = str(result.get("message") or result.get("error") or "") if result else ""
        if "access rate" in err_text.lower() or "access denied" in err_text.lower():
            MARGIN_CACHE[neg_key] = (0, time.time())
            return 0

        # Token expired — re-auth and retry once
        print(f"⚠️ Margin call failed for {symbol} (not rate-limit), re-authenticating...")
        auth_result = authenticate()
        if auth_result.get("ok"):
            with _cf.ThreadPoolExecutor(max_workers=1) as _pool2:
                _fut2 = _pool2.submit(_SMART_API.getMarginApi, payload)
                result = _fut2.result(timeout=15)
            if result and result.get("status") and result.get("data"):
                margin = result["data"].get("totalMarginRequired", 0)
                if margin > 0:
                    MARGIN_CACHE[cache_key] = (margin, time.time())
                return margin

        return 0
    except Exception as e:
        print(f"❌ Margin error for {symbol}: {e}")
        return 0

# ================================================================
# QUANTITY CALCULATION
# ================================================================

def calculate_qty(symbol, price, total_capital, num_parts=4, exchange="NSE"):
    """Calculate max quantity for a stock."""
    if not symbol or not price or total_capital <= 0:
        return 0

    margin = get_margin(symbol, price, quantity=1, exchange=exchange)
    if margin <= 0:
        return 0

    part_capital = total_capital / num_parts
    qty = math.floor(part_capital / margin)
    return max(qty, 0)

def calculate_quantities(symbols, prices, total_capital=100000, num_parts=4, exchange="NSE"):
    """Calculate quantities for multiple stocks in parallel (global timeout).

    Runs up to 5 margin queries in parallel.  If the full set does not
    finish within 12 seconds we return partial results — whatever margin
    values were cached or computed so far.  Un-computed stocks get
    quantity 0 on this pass but will be filled on the next page load as
    the 15-minute positive cache persists.
    """
    if not symbols or not prices or len(symbols) != len(prices):
        return {}

    results = {}

    def fetch(symbol, price):
        qty = calculate_qty(symbol, price, total_capital, num_parts, exchange)
        return symbol, qty

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch, sym, price): sym 
                  for sym, price in zip(symbols, prices)}
        remaining = set(futures.keys())
        deadline = time.monotonic() + 12.0

        for future in as_completed(futures):
            if time.monotonic() >= deadline:
                remaining.discard(future)
                break
            remaining.discard(future)
            try:
                rem = max(0.1, deadline - time.monotonic())
                symbol, qty = future.result(timeout=rem)
                results[symbol] = qty
            except Exception as e:
                symbol = futures.get(future, "?")
                print(f"❌ Error for {symbol}: {e}")
                results[symbol] = 0

        # Cancel stragglers and fill zeros
        for fut in remaining:
            fut.cancel()
        for sym in symbols:
            if sym not in results:
                results[sym] = 0

    return results

# ================================================================
# EXPORT FOR OTHER MODULES
# ================================================================

__all__ = [
    'ANGEL_PROXIES',
    '_CREDS',
    'set_credentials',
    'authenticate',
    'get_access_token',
    'is_connected',
    'disconnect',
    'set_feed_token',
    'get_feed_token',
    'get_token',
    'get_margin',
    'calculate_qty',
    'calculate_quantities',
]

# ================================================================
# MAIN - Test
# ================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("ANGEL ONE MARGIN CALCULATOR (with Proxy + Feed Token Support)")
    print("=" * 60)
    print(f"\n✅ Proxy configured: {ANGEL_PROXY_HOST}:{ANGEL_PROXY_PORT}")
    print(f"   (IP whitelisting enabled for all API calls)")
    print("\nAvailable functions:")
    print("  - set_credentials(api_key, client_id, password, totp_secret)")
    print("  - authenticate()  ← Returns feed_token in response")
    print("  - set_feed_token(feed_token)  ← Store feed token manually")
    print("  - get_feed_token()  ← Get stored feed token")
    print("  - calculate_quantities(symbols, prices, capital, parts)")
    print("  - get_token(symbol, exchange)")
    print("  - is_connected()")
    print("  - disconnect()")
    print("\nExample:")
    print("  set_credentials('API_KEY', 'CLIENT_ID', 'PASSWORD')")
    print("  auth = authenticate()")
    print("  if auth.get('ok'):")
    print("      feed_token = auth.get('feed_token')  # Auto-stored")
    print("      # OR manually:")
    print("      # set_feed_token('YOUR_FEED_TOKEN')")
    print("      results = calculate_quantities(['RELIANCE'], [2800], 100000)")
    print("      print(results)")