"""
Angel One SmartAPI WebSocket Live Market Feeds
"""
class AngelWebSocket:
    def __init__(self, auth_token, api_key, client_code, feed_token):
        self.auth_token = auth_token
        self.api_key = api_key
        self.client_code = client_code
        self.feed_token = feed_token

    def connect(self):
        print("Connecting to Angel One SmartAPI WebSocket...")
