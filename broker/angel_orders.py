# ==============================================================================
# ANGEL ONE ORDERS MODULE
#
# Direct order placement via Angel One SmartAPI Order API.
# 
# IMPORTANT: Angel One requires a whitelisted Static IP for Order Placement
# APIs specifically (not required for Margin Calculator / Fund Limit).
# Routes through the same static-IP proxy as the rest of the broker module.
# ==============================================================================

import requests
import json

from .angel_margin_calculator import (
    get_access_token,
    _CREDS,
    get_token,
    is_connected,
    ANGEL_PROXIES  # Import proxy configuration
)

# ==============================================================================
# ANGEL ONE API CONSTANTS
# ==============================================================================
ANGEL_BASE_URL = "https://apiconnect.angelbroking.com"
ANGEL_ORDER_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/order/v1/placeOrder"
ANGEL_ORDER_STATUS_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/order/v1/getOrderBook"
ANGEL_ORDER_CANCEL_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/order/v1/cancelOrder"

# ==============================================================================
# ORDER PLACEMENT
# ==============================================================================

def place_angel_order(
    symbol,
    quantity,
    transaction_type="BUY",
    product_type="INTRADAY",
    order_type="MARKET",
    price=0,
    trigger_price=0,
    after_market_order=False,
    exchange="NSE",
    validity="DAY"
):
    """
    Place a single order via Angel One SmartAPI (routed through the
    static-IP proxy, since Angel One requires IP whitelisting for order placement).

    Args:
        symbol (str): Stock symbol (e.g. "RELIANCE"). Resolved to Angel One's token.
        quantity (int): Number of shares to buy.
        transaction_type (str): "BUY" or "SELL" (default "BUY").
        product_type (str): "INTRADAY" (MIS) or "CNC" (delivery) or "MARGIN".
                           Default "INTRADAY".
        order_type (str): "MARKET" or "LIMIT" (default "MARKET").
        price (float): For LIMIT orders only (default 0).
        trigger_price (float): For SL-M or SL-L orders (default 0).
        after_market_order (bool): True for AMO, False for regular (default False).
        exchange (str): "NSE" or "BSE" (default "NSE").
        validity (str): "DAY" or "IOC" (default "DAY").

    Returns:
        dict: {"success": bool,
               "order_id": str|None,
               "error": str|None,
               "symbol": str,
               ...}
    """
    symbol_upper = str(symbol or "").strip().upper()
    if not symbol_upper:
        return {"success": False, "error": "symbol is empty", "symbol": symbol}

    # Step 1: Check connection
    if not is_connected():
        return {
            "success": False,
            "error": "Not connected to Angel One. Call authenticate() first.",
            "symbol": symbol_upper
        }

    # Step 2: Resolve symbol → token (uses cached scrip master)
    try:
        token = get_token(symbol_upper, exchange)
    except Exception as exc:
        return {
            "success": False,
            "error": f"token lookup failed: {type(exc).__name__}: {exc}",
            "symbol": symbol_upper,
        }

    if not token:
        return {
            "success": False,
            "error": f"Symbol '{symbol_upper}' not found in Angel One scrip master",
            "symbol": symbol_upper,
        }

    # Step 3: Validate quantity
    if not quantity or int(quantity) < 1:
        return {"success": False, "error": "Invalid quantity", "symbol": symbol_upper}

    # Step 4: Get credentials
    api_key = _CREDS.get("api_key", "")
    access_token = get_access_token()

    if not api_key or not access_token:
        return {
            "success": False,
            "error": "API Key or Access Token not set",
            "symbol": symbol_upper,
        }

    # Step 5: Map product types for Angel One
    product_map = {
        "INTRADAY": "INTRADAY",
        "CNC": "CNC",
        "MARGIN": "MARGIN"
    }

    product = product_map.get(product_type, "INTRADAY")

    # Step 6: Map order types for Angel One
    order_type_map = {
        "MARKET": "MARKET",
        "LIMIT": "LIMIT",
        "SL": "STOP_LOSS",
        "SL_M": "STOP_LOSS_MARKET",
        "SL_L": "STOP_LOSS_LIMIT"
    }

    angel_order_type = order_type_map.get(order_type, "MARKET")

    # Step 7: Compose order payload for Angel One
    payload = {
        "symbol": symbol_upper,
        "token": str(token),
        "exchange": exchange,
        "transaction_type": transaction_type,  # BUY or SELL
        "product_type": product,               # INTRADAY, CNC, MARGIN
        "order_type": angel_order_type,        # MARKET, LIMIT, STOP_LOSS
        "quantity": int(quantity),
        "price": float(price),
        "trigger_price": float(trigger_price),
        "validity": validity,                   # DAY or IOC
        "amo": "YES" if after_market_order else "NO",
    }

    # Remove fields with 0 values to avoid API errors
    if payload["price"] == 0:
        del payload["price"]
    if payload["trigger_price"] == 0:
        del payload["trigger_price"]

    headers = {
        "X-API-Key": api_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    # Step 8: Submit order through proxy (IP whitelisting required)
    try:
        print(f"📤 Placing Angel One order for {symbol_upper}")
        print(f"   Payload: {json.dumps(payload, indent=2)}")
        print(f"   Using proxy: {ANGEL_PROXIES.get('http', 'No proxy')}")

        response = requests.post(
            ANGEL_ORDER_URL,
            json=payload,
            headers=headers,
            proxies=ANGEL_PROXIES,  # ← IMPORTANT: Proxy for IP whitelisting
            timeout=10
        )

        print(f"📥 Response status: {response.status_code}")

        # Step 9: Handle response
        if response.status_code not in (200, 201, 202):
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:300]}",
                "symbol": symbol_upper,
            }

        try:
            data = response.json()
            print(f"📦 Response: {json.dumps(data, indent=2)}")
        except Exception as exc:
            return {
                "success": False,
                "error": f"Angel One returned non-JSON: {response.text[:300]} ({exc})",
                "symbol": symbol_upper,
            }

        # Step 10: Check for success
        if data.get("status") and data.get("data"):
            order_data = data["data"]
            order_id = order_data.get("orderid") or order_data.get("orderId")

            if order_id:
                return {
                    "success": True, 
                    "order_id": str(order_id), 
                    "symbol": symbol_upper,
                    "data": order_data
                }
            else:
                return {
                    "success": False,
                    "error": f"Angel One returned no orderId: {data}",
                    "symbol": symbol_upper,
                }
        else:
            error_msg = data.get("message", "Order placement failed")
            return {
                "success": False,
                "error": error_msg,
                "symbol": symbol_upper,
                "data": data
            }

    except requests.exceptions.Timeout:
        return {
            "success": False,
            "error": "Request timeout - Angel One API not responding",
            "symbol": symbol_upper,
        }
    except requests.exceptions.ConnectionError as e:
        return {
            "success": False,
            "error": f"Connection error: {str(e)}",
            "symbol": symbol_upper,
        }
    except Exception as exc:
        return {
            "success": False,
            "error": f"Exception: {type(exc).__name__}: {exc}",
            "symbol": symbol_upper,
        }

# ==============================================================================
# GET ORDER STATUS
# ==============================================================================

def get_order_status():
    """
    Get all orders for the current session (routed through proxy for IP whitelisting).

    Returns:
        dict: {"success": bool, "orders": list|None, "error": str|None}
    """
    if not is_connected():
        return {
            "success": False,
            "error": "Not connected to Angel One. Call authenticate() first."
        }

    api_key = _CREDS.get("api_key", "")
    access_token = get_access_token()

    if not api_key or not access_token:
        return {
            "success": False,
            "error": "API Key or Access Token not set"
        }

    headers = {
        "X-API-Key": api_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(
            ANGEL_ORDER_STATUS_URL,
            headers=headers,
            proxies=ANGEL_PROXIES,  # ← Proxy for IP whitelisting
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if data.get("status") and data.get("data"):
                return {
                    "success": True,
                    "orders": data["data"]
                }
            else:
                return {
                    "success": False,
                    "error": data.get("message", "Failed to get orders")
                }
        else:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}"
            }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Exception: {type(exc).__name__}: {exc}"
        }

# ==============================================================================
# CANCEL ORDER
# ==============================================================================

def cancel_angel_order(order_id):
    """
    Cancel an existing order (routed through proxy for IP whitelisting).

    Args:
        order_id (str): The order ID to cancel.

    Returns:
        dict: {"success": bool, "error": str|None, "order_id": str}
    """
    if not is_connected():
        return {
            "success": False,
            "error": "Not connected to Angel One. Call authenticate() first."
        }

    if not order_id:
        return {
            "success": False,
            "error": "order_id is required"
        }

    api_key = _CREDS.get("api_key", "")
    access_token = get_access_token()

    if not api_key or not access_token:
        return {
            "success": False,
            "error": "API Key or Access Token not set"
        }

    payload = {
        "orderid": str(order_id)
    }

    headers = {
        "X-API-Key": api_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            ANGEL_ORDER_CANCEL_URL,
            json=payload,
            headers=headers,
            proxies=ANGEL_PROXIES,  # ← Proxy for IP whitelisting
            timeout=10
        )

        if response.status_code in (200, 201, 202):
            data = response.json()
            if data.get("status"):
                return {
                    "success": True,
                    "order_id": str(order_id)
                }
            else:
                return {
                    "success": False,
                    "error": data.get("message", "Cancellation failed"),
                    "order_id": str(order_id)
                }
        else:
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:200]}",
                "order_id": str(order_id)
            }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Exception: {type(exc).__name__}: {exc}",
            "order_id": str(order_id)
        }

# ==============================================================================
# MODIFY ORDER (Placeholder)
# ==============================================================================

def modify_angel_order(order_id, quantity=None, price=None, trigger_price=None):
    """
    Modify an existing order.
    Note: Angel One may or may not support order modification.
    This is a placeholder.

    Args:
        order_id (str): The order ID to modify.
        quantity (int): New quantity (optional).
        price (float): New price (optional).
        trigger_price (float): New trigger price (optional).

    Returns:
        dict: {"success": bool, "error": str|None}
    """
    return {
        "success": False,
        "error": "Order modification not yet implemented for Angel One"
    }

# ==============================================================================
# TEST FUNCTION
# ==============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("ANGEL ONE ORDER MODULE (with Proxy Support)")
    print("=" * 60)
    print("\n✅ IP Whitelisting via Static Proxy Enabled")
    print(f"   Proxy: {ANGEL_PROXIES.get('http', 'Not configured')}")
    print("\nAvailable functions:")
    print("  - place_angel_order(symbol, quantity, ...)")
    print("  - get_order_status()")
    print("  - cancel_angel_order(order_id)")
    print("\nExample:")
    print("  from angel_margin_calculator import set_credentials, authenticate")
    print("  from angel_orders import place_angel_order")
    print("")
    print("  # Connect first")
    print("  set_credentials('API_KEY', 'CLIENT_ID', 'PASSWORD')")
    print("  authenticate()")
    print("")
    print("  # Place order (automatically uses proxy for IP whitelisting)")
    print("  result = place_angel_order('RELIANCE', 10)")
    print("  print(result)")