# broker/dhan.py
# Complete Dhan API integration - Token, Margin, Order, Batch + Parallel

import requests
import pyotp
import math
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from .config import *
from .utils import get_security_id_map

# ─── SESSION CACHE ───
_token_cache = None
_token_expiry = None
_margin_cache = {}  # symbol -> margin (24-hour cache)
_margin_cache_time = {}


def get_access_token():
    """
    Get valid Dhan access token.
    Auto-generates via TOTP if expired.
    Cached for 23 hours.
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
            print(f"Token generation failed: HTTP {response.status_code}")
            return None
        
        data = response.json()
        token = data.get("accessToken")
        
        if not token:
            print(f"No accessToken in response: {data}")
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
    # Check cache first (24 hours)
    symbol_key = symbol.upper()
    if symbol_key in _margin_cache and symbol_key in _margin_cache_time:
        if datetime.now() - _margin_cache_time[symbol_key] < timedelta(hours=24):
            return _margin_cache[symbol_key]
    
    token = get_access_token()
    if not token:
        return None
    
    # Get security ID
    security_map = get_security_id_map()
    security_id = security_map.get(symbol_key)
    
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
        
        if response.status_code == 401:
            # Token expired - refresh and retry once
            _token_cache = None
            token = get_access_token()
            if not token:
                return None
            headers["access-token"] = token
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
        
        if margin is None:
            return None
        
        margin = float(margin)
        
        # Cache the margin
        _margin_cache[symbol_key] = margin
        _margin_cache_time[symbol_key] = datetime.now()
        
        return margin
        
    except Exception as e:
        print(f"Margin error for {symbol}: {e}")
        return None


def get_margins_batch_parallel(symbols, prices, max_workers=10):
    """
    Fetch margins for multiple stocks in parallel.
    Returns: dict {symbol: margin}
    """
    if not symbols or not prices:
        return {}
    
    results = {}
    
    def fetch_one(symbol, price):
        symbol = symbol.upper()
        margin = get_margin_per_share(symbol, price)
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
            except Exception as e:
                print(f"Error in parallel fetch: {e}")
                continue
    
    return results


def calculate_max_qty_batch(stocks_data, total_capital=100000, num_parts=4, max_stocks=20):
    """
    Calculate MaxQty for a list of stocks using parallel processing.
    
    Args:
        stocks_data: list of dict with 'Symbol' and 'Price'
        total_capital: Total trading capital
        num_parts: Number of parts to divide capital into
        max_stocks: Maximum stocks to calculate for (default 20)
    
    Returns:
        list: stocks_data with 'MaxQty' added
    """
    if not stocks_data:
        return stocks_data
    
    # Take only max_stocks stocks for calculation
    first_n = stocks_data[:max_stocks]
    remaining = stocks_data[max_stocks:]
    
    # Extract symbols and prices
    symbols = [item["Symbol"] for item in first_n]
    prices = [item["Price"] for item in first_n]
    
    # Get margins in parallel
    margins = get_margins_batch_parallel(symbols, prices)
    
    # Calculate MaxQty
    part_capital = total_capital / num_parts
    
    for item in first_n:
        symbol = item["Symbol"]
        price = item["Price"]
        margin = margins.get(symbol)
        
        if margin and margin > 0 and price > 0:
            max_qty = math.floor(part_capital / margin)
            item["MaxQty"] = max(max_qty, 0)
        else:
            item["MaxQty"] = 0
    
    # For remaining stocks, set MaxQty = 0
    for item in remaining:
        item["MaxQty"] = 0
    
    return stocks_data


def place_order(symbol, quantity, product_type="INTRADAY", order_type="MARKET"):
    """
    Place an order on Dhan.
    
    Args:
        symbol: Stock symbol (e.g., "RELIANCE")
        quantity: Number of shares
        product_type: "INTRADAY" or "CNC"
        order_type: "MARKET" or "LIMIT"
    
    Returns:
        dict: {"success": bool, "order_id": str, "error": str}
    """
    token = get_access_token()
    if not token:
        return {"success": False, "error": "No access token available"}
    
    # Get security ID
    security_map = get_security_id_map()
    security_id = security_map.get(symbol.upper())
    
    if not security_id:
        return {"success": False, "error": f"Symbol {symbol} not found"}
    
    try:
        payload = {
            "dhanClientId": DHAN_CLIENT_ID,
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "quantity": int(quantity),
            "productType": product_type,
            "orderType": order_type,
            "securityId": str(security_id),
            "validity": "DAY"
        }
        
        # Add price if LIMIT order
        if order_type == "LIMIT":
            payload["price"] = 0  # User should provide this
        
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
        
        if response.status_code == 401:
            # Token expired - refresh and retry once
            _token_cache = None
            token = get_access_token()
            if not token:
                return {"success": False, "error": "Token refresh failed"}
            headers["access-token"] = token
            response = requests.post(
                DHAN_ORDER_URL,
                json=payload,
                headers=headers,
                proxies=DHAN_PROXIES,
                timeout=10
            )
        
        if response.status_code != 200:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }
        
        data = response.json()
        return {
            "success": True,
            "order_id": data.get("orderId", "N/A"),
            "data": data
        }
        
    except Exception as e:
        return {"success": False, "error": str(e)}


def place_batch_orders(orders):
    """
    Place multiple orders in parallel.
    
    Args:
        orders: list of dict with 'symbol', 'quantity', 'product_type'
    
    Returns:
        list of dict with order results
    """
    results = []
    
    def place_one(order):
        return place_order(
            order.get('symbol'),
            order.get('quantity'),
            order.get('product_type', 'INTRADAY')
        )
    
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(place_one, order): order
            for order in orders
        }
        
        for future in as_completed(futures):
            order = futures[future]
            try:
                result = future.result()
                results.append({
                    'symbol': order.get('symbol'),
                    'quantity': order.get('quantity'),
                    **result
                })
            except Exception as e:
                results.append({
                    'symbol': order.get('symbol'),
                    'quantity': order.get('quantity'),
                    'success': False,
                    'error': str(e)
                })
    
    return results


def get_fund_limit():
    """
    Fetch available trading balance from Dhan.
    Returns: balance (float) or None
    """
    token = get_access_token()
    if not token:
        return None
    
    try:
        headers = {
            "Content-Type": "application/json",
            "accept-token": token
        }
        
        response = requests.get(
            "https://api.dhan.co/v2/fundlimit",
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10
        )
        
        if response.status_code != 200:
            return None
        
        data = response.json()
        balance = data.get("availabelBalance", data.get("availableBalance"))
        
        return float(balance) if balance is not None else None
        
    except Exception as e:
        print(f"Fund limit error: {e}")
        return None


# ─── CLEAR CACHE ───
def clear_margin_cache():
    """Clear the margin cache."""
    global _margin_cache, _margin_cache_time
    _margin_cache = {}
    _margin_cache_time = {}


def get_cache_stats():
    """Get cache statistics."""
    return {
        'margin_cache_size': len(_margin_cache),
        'token_valid': _token_cache is not None and datetime.now() < _token_expiry if _token_expiry else False
    }
