"""
TradingView OHLC WebSocket Streamer
"""
import json
import logging

logger = logging.getLogger(__name__)

class TradingViewWebSocket:
    def __init__(self, symbols=None):
        self.symbols = symbols or []

    def stream_candles(self):
        logger.info(f"Streaming candles for symbols: {self.symbols}")
