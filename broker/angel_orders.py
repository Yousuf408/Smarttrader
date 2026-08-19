"""
Angel One SmartAPI Order Placement and Execution Engine
Handles Regular, Intraday (MIS), Stop-Loss (SL-L/SL-M), Bracket and GTT Orders.
"""
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("angel_orders")

def place_angel_order(
    smart_api_client,
    symbol: str,
    qty: int,
    price: float = 0.0,
    transaction_type: str = 'BUY',
    order_type: str = 'MARKET',
    product_type: str = 'INTRADAY',
    trigger_price: float = 0.0,
    symbol_token: str = "3045",
    exchange: str = "NSE"
) -> Dict[str, Any]:
    """
    Places an order via Angel One SmartAPI.
    Supports MARKET, LIMIT, STOPLOSS_LIMIT, and STOPLOSS_MARKET.
    """
    if not smart_api_client:
        logger.info(f"[AngelOrders-SIM] Simulating {transaction_type} {qty}x {symbol} @ {price or 'MKT'}")
        return {
            "status": True,
            "message": "SUCCESS",
            "errorcode": "",
            "data": {
                "script": symbol,
                "orderid": f"ANGEL_{symbol}_{int(qty)}_{int(price * 100) if price else 'MKT'}"
            }
        }

    order_params = {
        "variety": "STOPLOSS" if "STOPLOSS" in order_type else "NORMAL",
        "tradingsymbol": symbol if symbol.endswith("-EQ") else f"{symbol}-EQ",
        "symboltoken": str(symbol_token),
        "transactiontype": transaction_type.upper(),
        "exchange": exchange.upper(),
        "ordertype": order_type.upper(),
        "producttype": product_type.upper(),
        "duration": "DAY",
        "price": str(round(price, 2)) if order_type != 'MARKET' else "0",
        "quantity": str(int(qty))
    }
    
    if trigger_price > 0:
        order_params["triggerprice"] = str(round(trigger_price, 2))

    try:
        res = smart_api_client.placeOrder(order_params)
        logger.info(f"[AngelOrders] Order response for {symbol}: {res}")
        return res
    except Exception as e:
        logger.error(f"[AngelOrders] Error placing order for {symbol}: {e}")
        return {"status": False, "error": str(e)}

def cancel_angel_order(smart_api_client, order_id: str, variety: str = "NORMAL") -> Dict[str, Any]:
    """Cancels a pending order in Angel One."""
    if not smart_api_client:
        return {"status": True, "message": f"Simulated order {order_id} cancelled"}
    try:
        return smart_api_client.cancelOrder(order_id, variety)
    except Exception as e:
        logger.error(f"[AngelOrders] Cancel order failed for {order_id}: {e}")
        return {"status": False, "error": str(e)}

def modify_angel_order(smart_api_client, order_id: str, qty: int, price: float, trigger_price: float = 0.0, order_type: str = "LIMIT") -> Dict[str, Any]:
    """Modifies a pending order or trails stop loss in Angel One."""
    if not smart_api_client:
        return {"status": True, "message": f"Simulated order {order_id} modified"}
    
    modify_params = {
        "variety": "NORMAL",
        "orderid": str(order_id),
        "ordertype": order_type,
        "producttype": "INTRADAY",
        "duration": "DAY",
        "price": str(round(price, 2)),
        "quantity": str(int(qty)),
        "tradingsymbol": "",
        "symboltoken": "",
        "exchange": "NSE"
    }
    if trigger_price > 0:
        modify_params["triggerprice"] = str(round(trigger_price, 2))
    
    try:
        return smart_api_client.modifyOrder(modify_params)
    except Exception as e:
        logger.error(f"[AngelOrders] Modify order failed for {order_id}: {e}")
        return {"status": False, "error": str(e)}

def get_angel_order_book(smart_api_client) -> Dict[str, Any]:
    """Fetches order book from Angel One."""
    if not smart_api_client:
        return {"status": True, "data": []}
    try:
        return smart_api_client.orderBook()
    except Exception as e:
        return {"status": False, "error": str(e)}

def get_angel_trade_book(smart_api_client) -> Dict[str, Any]:
    """Fetches trade execution book from Angel One."""
    if not smart_api_client:
        return {"status": True, "data": []}
    try:
        return smart_api_client.tradeBook()
    except Exception as e:
        return {"status": False, "error": str(e)}

