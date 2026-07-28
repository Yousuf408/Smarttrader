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
}

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

def authenticate():
    """Authenticate with Angel One and get access token."""
    api_key = _CREDS.get("api_key")
    client_id = _CREDS.get("client_id")
    password = _CREDS.get("password")
    totp_secret = _CREDS.get("totp_secret")

    if not api_key or not client_id or not password:
        return {"ok": False, "error": "Missing credentials. Call set_credentials() first."}

    print(f"🔐 Authenticating Angel One for: {client_id}")

    try:
        headers = {
            "X-API-Key": api_key,
            "X-Client-ID": client_id,
            "Content-Type": "application/json"
        }

        payload = {"clientid": client_id, "password": password}

        # Add TOTP if available
        if totp_secret:
            try:
                import pyotp
                totp = pyotp.TOTP(totp_secret)
                payload["totp"] = totp.now()
                print("✅ TOTP generated")
            except ImportError:
                print("⚠️ pyotp not installed - install with: pip install pyotp")
            except Exception as e:
                print(f"⚠️ TOTP error: {e}")

        # Try direct first, fall back to proxy if direct fails.
        # The proxy is primarily for Dhan IP whitelisting; Angel One
        # may or may not need it depending on whether the server IP
        # is whitelisted with Angel One's support team.
        try:
            response = requests.post(
                ANGEL_LOGIN_URL,
                json=payload,
                headers=headers,
                timeout=15,
            )
            if "Request Rejected" in response.text:
                # WAF block — retry through the proxy (if it helps)
                response = requests.post(
                    ANGEL_LOGIN_URL,
                    json=payload,
                    headers=headers,
                    proxies=ANGEL_PROXIES,
                    timeout=15,
                )
        except requests.ConnectionError:
            # Direct connection failed — try through proxy
            response = requests.post(
                ANGEL_LOGIN_URL,
                json=payload,
                headers=headers,
                proxies=ANGEL_PROXIES,
                timeout=15,
            )

        if response.status_code == 200:
            try:
                data = response.json()
            except json.JSONDecodeError:
                body_preview = (response.text or "")[:200]
                print(f"❌ Angel One returned 200 with empty/non-JSON body: {body_preview}")
                if "Request Rejected" in body_preview:
                    return {
                        "ok": False,
                        "error": (
                            "Angel One WAF blocked the request. This server's IP "
                            "needs to be whitelisted with Angel One support. "
                            "Provide them with the proxy IP 151.242.178.149."
                        ),
                    }
                return {
                    "ok": False,
                    "error": (
                        "Angel One auth returned empty response. "
                        "Check your credentials or try again."
                    ),
                }

            if data.get("status") and data.get("data"):
                access_token = data["data"].get("access_token")
                refresh_token = data["data"].get("refresh_token")
                feed_token = data["data"].get("feedToken") or data["data"].get("feed_token")

                if access_token:
                    _CREDS["access_token"] = access_token
                    _CREDS["refresh_token"] = refresh_token or ""

                    # Store feed token if available
                    if feed_token:
                        _CREDS["feed_token"] = str(feed_token)
                        print(f"✅ Feed token received and stored")

                    print("✅ Authentication successful!")
                    return {
                        "ok": True, 
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "feed_token": feed_token
                    }

            error = data.get("message", "Authentication failed")
            return {"ok": False, "error": error}
        else:
            return {"ok": False, "error": f"HTTP {response.status_code}: {response.text[:100]}"}

    except Exception as e:
        return {"ok": False, "error": str(e)}

def disconnect():
    """Clear all credentials."""
    for key in _CREDS:
        _CREDS[key] = ""
    print("🔌 Disconnected from Angel One")
    return {"ok": True}

# ================================================================
# INSTRUMENT MASTER
# ================================================================

def load_master():
    """Load Angel One scrip master."""
    global MASTER_CACHE, MASTER_CACHE_TIME

    if MASTER_CACHE and (time.time() - MASTER_CACHE_TIME) < MASTER_TTL:
        return MASTER_CACHE

    try:
        # Use proxy for scrip master (IP whitelisting)
        response = requests.get(
            ANGEL_SCRIP_MASTER_URL, 
            proxies=ANGEL_PROXIES,  # ← Proxy for IP whitelisting
            timeout=30
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

def get_token(symbol, exchange="NSE"):
    """Get security token for symbol."""
    cache_key = f"{exchange}:{symbol.upper()}"
    if cache_key in SECURITY_CACHE:
        return SECURITY_CACHE[cache_key]

    df = load_master()
    if df is None:
        return None

    # Find columns
    symbol_col = next((c for c in df.columns if 'symbol' in c.lower()), None)
    token_col = next((c for c in df.columns if 'token' in c.lower()), None)
    exch_col = next((c for c in df.columns if 'exchange' in c.lower()), None)

    if not symbol_col or not token_col:
        print("❌ Required columns not found in master")
        return None

    # Filter
    if exch_col:
        filtered = df[(df[exch_col].astype(str).str.upper() == exchange.upper()) & 
                     (df[symbol_col].astype(str).str.upper() == symbol.upper())]
    else:
        filtered = df[df[symbol_col].astype(str).str.upper() == symbol.upper()]

    if filtered.empty:
        print(f"⚠️ Symbol {symbol} not found in master")
        return None

    token = str(filtered[token_col].values[0])
    SECURITY_CACHE[cache_key] = token
    print(f"✅ Token for {symbol}: {token}")
    return token

# ================================================================
# MARGIN CALCULATION
# ================================================================

def get_margin(symbol, price, quantity=1, exchange="NSE", product_type="INTRADAY"):
    """Get margin for a single stock."""
    # Check cache
    cache_key = (symbol, round(float(price), 2))
    cached = MARGIN_CACHE.get(cache_key)
    if cached and (time.time() - cached[1]) < MARGIN_CACHE_TTL:
        return cached[0]

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
        headers = {
            "X-API-Key": _CREDS["api_key"],
            "Authorization": f"Bearer {_CREDS['access_token']}",
            "Content-Type": "application/json"
        }

        payload = {
            "positions": [{
                "symbol": symbol,
                "token": token,
                "exchange": exchange,
                "product_type": product_type,
                "transaction_type": "BUY",
                "price": float(price),
                "quantity": quantity
            }]
        }

        # Try direct first, fall back to proxy if WAF blocks
        try:
            response = requests.post(
                ANGEL_MARGIN_URL,
                json=payload,
                headers=headers,
                timeout=10,
            )
            if "Request Rejected" in (response.text or ""):
                response = requests.post(
                    ANGEL_MARGIN_URL,
                    json=payload,
                    headers=headers,
                    proxies=ANGEL_PROXIES,
                    timeout=10,
                )
        except requests.ConnectionError:
            response = requests.post(
                ANGEL_MARGIN_URL,
                json=payload,
                headers=headers,
                proxies=ANGEL_PROXIES,
                timeout=10,
            )

        if response.status_code == 200:
            data = response.json()
            if data.get("status") and data.get("data"):
                positions = data["data"].get("positions", [])
                if positions:
                    margin = positions[0].get("total_margin", 0)
                    if margin > 0:
                        MARGIN_CACHE[cache_key] = (margin, time.time())
                    return margin
        elif response.status_code == 401:
            # Token expired - reconnect
            print("⚠️ Token expired, reconnecting...")
            auth_result = authenticate()
            if auth_result.get("ok"):
                return get_margin(symbol, price, quantity, exchange, product_type)

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
    """Calculate quantities for multiple stocks in parallel."""
    if not symbols or not prices or len(symbols) != len(prices):
        return {}

    results = {}

    def fetch(symbol, price):
        qty = calculate_qty(symbol, price, total_capital, num_parts, exchange)
        return symbol, qty

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch, sym, price): sym 
                  for sym, price in zip(symbols, prices)}

        for future in as_completed(futures):
            try:
                symbol, qty = future.result()
                results[symbol] = qty
            except Exception as e:
                symbol = futures[future]
                print(f"❌ Error for {symbol}: {e}")
                results[symbol] = 0

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