# broker/dhan.py
# All Dhan API calls - Token, Margin, Order

import requests
import pyotp
import math
from datetime import datetime, timedelta
from .config import *
from .utils import get_security_id_map

# ─── SESSION CACHE ───
_token_cache = None
_token_expiry = None

def get_access_token():
    """
    Get valid Dhan access token.
    Auto-generates via TOTP if expired.
    """
    global _token_cache, _token_expiry
    
    now = datetime.now()
    
    # Return cached token if valid
    if _token_cache is not None and _token_expiry is not None:
        if now < _token_expiry:
            return _token_cache
    
    # Generate new token
    try:
        totp = pyotp.TOTP(DHAN_TOTP_SECRET).now()
        
        response = requests.post(
            DHAN_TOKEN_URL,
            params={
                "dhanClientId": DHAN_CLIENT_ID,
                "pin": DHAN_PIN,
                "totp": totp
            },
            proxies=DHAN_PROXIES,
            timeout=10
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        token = data.get("accessToken")
        
        if not token:
            return None
        
        # Cache token (expires in 23 hours)
        _token_cache = token
        _token_expiry = now + timedelta(hours=23)
        
        return token
        
    except Exception as e:
        print(f"Token generation error: {e}")
        return None


def get_margin_per_share(symbol, price):
    """
    Get margin required to buy 1 share of a stock.
    Returns: margin_per_share (float) or None
    """
    token = get_access_token()
    if not token:
        return None
    
    # Get security ID
    security_map = get_security_id_map()
    security_id = security_map.get(symbol.upper())
    
    if not security_id:
        return None
    
    try:
        payload = {
            "dhanClientId": DHAN_CLIENT_ID,
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "quantity": 1,
            "productType": "INTRADAY",
            "securityId": str(security_id),
            "price": float(price),
            "triggerPrice": 0
        }
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": token
        }
        
        response = requests.post(
            DHAN_MARGIN_URL,
            json=payload,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        margin = data.get("totalMargin")
        
        return float(margin) if margin is not None else None
        
    except Exception as e:
        print(f"Margin error for {symbol}: {e}")
        return None


def calculate_max_qty(symbol, price, total_capital=100000, num_parts=4):
    """
    Calculate max quantity for a stock.
    Returns: max_qty (int)
    """
    if price <= 0:
        return 0
    
    # Get margin per share
    margin = get_margin_per_share(symbol, price)
    
    if margin is None or margin <= 0:
        return 0
    
    # Calculate
    part_capital = total_capital / num_parts
    max_qty = math.floor(part_capital / margin)
    
    return max(0, max_qty)


def get_margins_batch(symbols, prices):
    """
    Get margins for multiple stocks at once.
    Returns: dict {symbol: margin}
    """
    results = {}
    for symbol, price in zip(symbols, prices):
        margin = get_margin_per_share(symbol, price)
        if margin is not None:
            results[symbol] = margin
    return results


def place_order(symbol, quantity, product_type="INTRADAY"):
    """
    Place an order on Dhan.
    Returns: dict with success/error
    """
    token = get_access_token()
    if not token:
        return {"success": False, "error": "No access token"}
    
    # Get security ID
    security_map = get_security_id_map()
    security_id = security_map.get(symbol.upper())
    
    if not security_id:
        return {"success": False, "error": "Symbol not found"}
    
    try:
        payload = {
            "dhanClientId": DHAN_CLIENT_ID,
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "quantity": int(quantity),
            "productType": product_type,
            "orderType": "MARKET",
            "securityId": str(security_id),
            "validity": "DAY"
        }
        
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": token
        }
        
        response = requests.post(
            DHAN_ORDER_URL,
            json=payload,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10
        )
        
        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}
        
        data = response.json()
        return {
            "success": True,
            "order_id": data.get("orderId", "N/A"),
            "data": data
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}
