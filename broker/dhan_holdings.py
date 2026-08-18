"""
Dhan Broker Holdings API Handler
"""
import os
import requests

def get_dhan_holdings(client_id=None, access_token=None):
    client_id = client_id or os.environ.get('DHAN_CLIENT_ID')
    access_token = access_token or os.environ.get('DHAN_ACCESS_TOKEN')
    headers = {
        'access-token': access_token,
        'client-id': client_id,
        'Content-Type': 'application/json'
    }
    url = 'https://api.dhan.co/v2/holdings'
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        return resp.json()
    except Exception as e:
        return {"error": str(e)}
