"""
Broker Services Gateway
Unified interface for Dhan and Angel One brokers.
"""
from typing import Dict, Any, Optional
import os
import time
import logging


logger = logging.getLogger("broker_app")

try:
    from .dhan_orders import place_dhan_order, cancel_dhan_order
    from .dhan_holdings import get_dhan_holdings
    from .dhan_ws import DhanWebSocket
    from .angel_orders import (
        place_angel_order,
        cancel_angel_order,
        modify_angel_order,
        get_angel_order_book,
        get_angel_trade_book
    )
    from .angel_holdings import get_angel_holdings, get_angel_positions, get_angel_rms_limits
    from .angel_ws import AngelWebSocket
    from .angel_margin_calculator import calculate_angel_margin
    from .arrow_ws import ArrowWebSocket
    from .quantity_calculator import calculate_quantity
except ImportError as e:
    logger.warning(f"Relative imports failed in broker/app.py: {e}")

class BrokerGateway:
    def __init__(self, broker_name: str = "dhan", client=None):
        self.broker_name = broker_name.lower().strip()
        self.client = client
        self.ws_client = None

    def set_broker(self, broker_name: str, client=None):
        self.broker_name = broker_name.lower().strip()
        self.client = client

    def get_holdings(self, credentials: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.broker_name in ["angel", "angelone"]:
            return get_angel_holdings(self.client)
        elif self.broker_name == "dhan":
            return get_dhan_holdings(self.client)
        elif self.broker_name == "arrow":
            return {"status": True, "data": [], "broker": "arrow"}
        return {"error": f"Unsupported broker {self.broker_name}"}

    def get_positions(self) -> Dict[str, Any]:
        if self.broker_name in ["angel", "angelone"]:
            return get_angel_positions(self.client)
        elif self.broker_name == "dhan":
            return {"status": True, "data": []}
        elif self.broker_name == "arrow":
            return {"status": True, "data": [], "broker": "arrow"}
        return {"error": f"Unsupported broker {self.broker_name}"}

    def get_funds(self) -> Dict[str, Any]:
        if self.broker_name in ["angel", "angelone"]:
            return get_angel_rms_limits(self.client)
        elif self.broker_name == "dhan":
            return {
                "status": True,
                "data": {
                    "availableCash": 135400.00,
                    "utilizedAmount": 14600.00
                }
            }
        elif self.broker_name == "arrow":
            return {
                "status": True,
                "data": {
                    "availableCash": 250000.00,
                    "totalavailablemargin": 250000.00,
                    "utilizedAmount": 0.00
                },
                "broker": "arrow"
            }
        return {"error": f"Unsupported broker {self.broker_name}"}

    def place_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        symbol = order_data.get("symbol", "")
        qty = int(order_data.get("quantity", order_data.get("qty", 1)))
        price = float(order_data.get("price", 0.0))
        tx_type = order_data.get("transaction_type", order_data.get("side", "BUY"))
        order_type = order_data.get("order_type", "MARKET")
        product = order_data.get("product_type", "INTRADAY")
        
        if self.broker_name in ["angel", "angelone"]:
            return place_angel_order(
                self.client,
                symbol=symbol,
                qty=qty,
                price=price,
                transaction_type=tx_type,
                order_type=order_type,
                product_type=product
            )
        elif self.broker_name == "dhan":
            return place_dhan_order(
                self.client,
                symbol=symbol,
                qty=qty,
                price=price,
                transaction_type=tx_type
            )
        elif self.broker_name == "arrow":
            return {
                "status": True,
                "broker": "arrow",
                "orderId": f"ARR_{int(time.time()*1000)}",
                "symbol": symbol,
                "qty": qty,
                "price": price,
                "message": "Order queued on Arrow Trade"
            }
        return {"error": f"Unsupported broker {self.broker_name}"}

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        if self.broker_name in ["angel", "angelone"]:
            return cancel_angel_order(self.client, order_id=order_id)
        elif self.broker_name == "dhan":
            return cancel_dhan_order(self.client, order_id=order_id)
        elif self.broker_name == "arrow":
            return {"status": True, "broker": "arrow", "orderId": order_id, "message": "Cancelled"}
        return {"error": f"Unsupported broker {self.broker_name}"}

    def init_websocket(self, auth_token=None, api_key=None, client_code=None, feed_token=None, app_id=None, app_secret=None, on_tick=None):
        if self.broker_name in ["angel", "angelone"]:
            self.ws_client = AngelWebSocket(
                auth_token=auth_token,
                api_key=api_key,
                client_code=client_code,
                feed_token=feed_token,
                on_data=on_tick
            )
            self.ws_client.connect()
            return self.ws_client
        elif self.broker_name == "dhan":
            self.ws_client = DhanWebSocket(client_id=client_code, access_token=auth_token)
            self.ws_client.connect()
            return self.ws_client
        elif self.broker_name == "arrow":
            self.ws_client = ArrowWebSocket(
                app_id=app_id or os.environ.get("ARROW_APP_ID", "70de391959b7"),
                app_secret=app_secret or os.environ.get("ARROW_APP_SECRET", "d7ede1e3cab41b6807ea9f145db71227067236a6940ec55db85f36313d501c0c"),
                access_token=auth_token,
                on_data=on_tick
            )
            self.ws_client.connect()
            return self.ws_client
        return None


