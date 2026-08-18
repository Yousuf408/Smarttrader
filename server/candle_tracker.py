"""
Server Candle Tracker and Cache Worker
"""
import time
import logging

logger = logging.getLogger(__name__)

class CandleTracker:
    def __init__(self):
        self.cache = {}

    def update_tick(self, symbol, price, volume):
        self.cache[symbol] = {"price": price, "volume": volume, "updated": time.time()}

    def get_candle(self, symbol):
        return self.cache.get(symbol)
