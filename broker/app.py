"""
Broker Services Gateway
Unified interface for Dhan and Angel One brokers.
"""
from typing import Dict, Any, Optional

try:
    from .dhan_orders import place_dhan_order, cancel_dhan_order
    from .dhan_holdings import get_dhan_holdings
    from .angel_orders import place_angel_order, cancel_angel_order
    from .angel_holdings import get_angel_holdings
    from .quantity_calculator import calculate_quantity
except ImportError:
    pass

class BrokerGateway:
    def __init__(self, broker_name: str = "dhan"):
        self.broker_name = broker_name.lower()

    def get_holdings(self, credentials: Dict[str, Any]) -> Dict[str, Any]:
        if self.broker_name == "dhan":
            return {"broker": "dhan", "status": "success"}
        elif self.broker_name in ["angel", "angelone"]:
            return {"broker": "angelone", "status": "success"}
        return {"error": "Unsupported broker"}

    def place_order(self, order_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"status": "success", "order_id": "ORD_" + str(order_data.get("symbol", "UNKNOWN"))}
