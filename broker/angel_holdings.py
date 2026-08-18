"""
Angel One SmartAPI Holdings Connector
"""
import os

def get_angel_holdings(smart_api_client=None):
    if smart_api_client:
        return smart_api_client.holding()
    return {"error": "Angel SmartAPI client not initialized"}
