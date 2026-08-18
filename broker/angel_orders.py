"""
Angel One SmartAPI Order Placement
"""
def place_angel_order(smart_api_client, symbol, qty, price, transaction_type='BUY'):
    if not smart_api_client:
        return {"error": "Angel client not initialized"}
    order_params = {
        "variety": "NORMAL",
        "tradingsymbol": symbol,
        "symboltoken": "3045",
        "transactiontype": transaction_type,
        "exchange": "NSE",
        "ordertype": "MARKET",
        "producttype": "INTRADAY",
        "duration": "DAY",
        "price": str(price),
        "quantity": str(qty)
    }
    return smart_api_client.placeOrder(order_params)
