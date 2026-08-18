"""
Dhan Broker Order Placement and Management
"""
import os
import requests

def place_dhan_order(symbol, qty, price, order_type='MARKET', transaction_type='BUY'):
    client_id = os.environ.get('DHAN_CLIENT_ID')
    access_token = os.environ.get('DHAN_ACCESS_TOKEN')
    headers = {
        'access-token': access_token,
        'client-id': client_id,
        'Content-Type': 'application/json'
    }
    payload = {
        "dhanClientId": client_id,
        "transactionType": transaction_type,
        "exchangeSegment": "NSE_EQ",
        "productType": "INTRADAY",
        "orderType": order_type,
        "validity": "DAY",
        "tradingSymbol": symbol,
        "quantity": int(qty),
        "price": float(price) if price else 0
    }
    url = 'https://api.dhan.co/v2/orders'
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}
