"""
Dhan WebSocket Live Quotes Streamer
"""
import os
import websocket
import json
import logging

logger = logging.getLogger(__name__)

class DhanWebSocket:
    def __init__(self, client_id=None, access_token=None):
        self.client_id = client_id or os.environ.get('DHAN_CLIENT_ID')
        self.access_token = access_token or os.environ.get('DHAN_ACCESS_TOKEN')
        self.ws_url = f"wss://api-feed.dhan.co?version=2&token={self.access_token}&clientId={self.client_id}&authType=2"

    def connect(self):
        logger.info("Connecting to Dhan WebSocket feed...")
