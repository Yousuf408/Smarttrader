# ==============================================================================
# TV SCREENER — DHAN ORDERS MODULE
#
# Direct order placement via DhanHQ Order API — bypasses AlgoMojo entirely
# for faster execution (one less network hop: App → Dhan, not App →
# AlgoMojo → Broker).
#
# Reuses auth and security_id lookup from quantity_calculator.py — does NOT
# duplicate that logic. Falls back to the module-level DHAN_ACCESS_TOKEN
# (set from os.environ["DHAN_ACCESS_TOKEN"] first; falls through to
# DHAN_MANUAL_ACCESS_TOKEN in quantity_calculator.py, which the user
# populated with a real token earlier in this session).
#
# IMPORTANT: DhanHQ requires a whitelisted Static IP for Order Placement
# APIs specifically (not required for Margin Calculator / Fund Limit).
# Routes through the same static-IP proxy as the rest of the broker
# module (configured in quantity_calculator.DHAN_PROXIES).
# ==============================================================================

import requests

from .quantity_calculator import (
    DHAN_ACCESS_TOKEN,
    DHAN_CLIENT_ID,
    DHAN_PROXIES,
    get_security_id,
)

DHAN_ORDER_URL = "https://api.dhan.co/v2/orders"


def place_dhan_order(
    symbol,
    quantity,
    transaction_type="BUY",
    product_type="INTRADAY",
    after_market_order=False,
    amo_time="OPEN",
):
    """Place a single order via the DhanHQ Order API (routed through the
    static-IP proxy, since Dhan requires IP whitelisting for order placement).

    Args:
        symbol (str): Stock symbol (e.g. "RELIANCE"). Resolved internally
                      to Dhan's security_id via quantity_calculator's
                      master-CSV cache.
        quantity (int): Number of shares to buy.
        transaction_type (str): "BUY" or "SELL" (default "BUY").
        product_type (str): "INTRADAY" (MIS, leveraged, auto-square-off
                           3:15 PM) or "CNC" (delivery, holds overnight).
                           Default "INTRADAY".
        after_market_order (bool): True to queue as AMO for 9:15 IST market
                                    open (use when calling outside market
                                    hours). Default False (live order).
        amo_time (str): "OPEN" / "OPEN_30" / "OPEN_60" — only meaningful
                        when after_market_order=True. Default "OPEN".

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

    # Step 1: Resolve symbol → security_id (uses cached master CSV).
    try:
        security_id = get_security_id(symbol_upper)
    except Exception as exc:
        return {
            "success": False,
            "error": f"security_id lookup failed: {type(exc).__name__}: {exc}",
            "symbol": symbol_upper,
        }
    if not security_id:
        return {
            "success": False,
            "error": "Symbol not found in Dhan instrument master",
            "symbol": symbol_upper,
        }

    if not quantity or int(quantity) < 1:
        return {"success": False, "error": "Invalid quantity", "symbol": symbol_upper}

    # Step 2: Access token (uses DHAN_ACCESS_TOKEN set by
    # quantity_calculator from env var or DHAN_MANUAL_ACCESS_TOKEN).
    access_token = (str(DHAN_ACCESS_TOKEN or "")).strip()
    if not access_token or access_token == "your_dhan_access_token_here":
        return {
            "success": False,
            "error": (
                "DHAN_ACCESS_TOKEN not set — export os.environ['DHAN_ACCESS_TOKEN'] "
                "or assign DHAN_MANUAL_ACCESS_TOKEN in broker/quantity_calculator.py"
            ),
            "symbol": symbol_upper,
        }

    # Step 3: Compose order payload. Dhan native JSON types — quantity as int,
    # price=0 as int (not string), disclosedQuantity=0 (numeric).
    payload = {
        "dhanClientId": str(DHAN_CLIENT_ID),
        "transactionType": transaction_type,
        "exchangeSegment": "NSE_EQ",
        "productType": product_type,
        "orderType": "MARKET",
        "validity": "DAY",
        "securityId": str(security_id),
        "quantity": int(quantity),
        "disclosedQuantity": 0,
        "price": 0,
        "triggerPrice": 0,
        "afterMarketOrder": bool(after_market_order),
    }
    # amoTime is only accepted by Dhan when afterMarketOrder=True.
    if after_market_order:
        payload["amoTime"] = amo_time

    headers = {
        "Content-Type": "application/json",
        "access-token": access_token,
    }

    # Step 4: Submit.
    try:
        response = requests.post(
            DHAN_ORDER_URL,
            json=payload,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10,
        )
        if response.status_code not in (200, 201, 202):
            return {
                "success": False,
                "error": f"HTTP {response.status_code}: {response.text[:300]}",
                "symbol": symbol_upper,
            }
        try:
            data = response.json()
        except Exception as exc:
            return {
                "success": False,
                "error": f"Dhan returned non-JSON: {response.text[:300]} ({exc})",
                "symbol": symbol_upper,
            }
        order_id = data.get("orderId")
        if not order_id:
            return {
                "success": False,
                "error": f"Dhan returned no orderId: {data}",
                "symbol": symbol_upper,
            }
        return {"success": True, "order_id": str(order_id), "symbol": symbol_upper}
    except Exception as exc:
        return {
            "success": False,
            "error": f"Exception: {type(exc).__name__}: {exc}",
            "symbol": symbol_upper,
        }
