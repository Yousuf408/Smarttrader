"""
Candle Recorder Daemon for Live Market Tracking & Advance ORB Strategy
Persists 5-minute and 15-minute 9:15 AM opening candle OHLC to JSON files.
"""
import os
import sys
import json
import time
import datetime
import logging
from typing import Dict, Any, List

logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [%(levelname)s] [CANDLE_RECORDER] %(message)s'
)
logger = logging.getLogger("candle_recorder")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
JSON_DIR = os.path.join(BASE_DIR, "json")
STOCKS_DIR = os.path.join(BASE_DIR, "stocks")

CANDLE_15M_FILE = os.path.join(JSON_DIR, "candle_15min.json")
CANDLE_5M_FILE = os.path.join(JSON_DIR, "candle_5min.json")

class CandleRecorder:
    def __init__(self):
        self.candles_5m: Dict[str, Dict[str, Any]] = {}
        self.candles_15m: Dict[str, Dict[str, Any]] = {}
        self.ensure_dirs()

    def ensure_dirs(self):
        os.makedirs(JSON_DIR, exist_ok=True)
        os.makedirs(STOCKS_DIR, exist_ok=True)

    def record_candle(self, symbol: str, timeframe: str, o: float, h: float, l: float, c: float, vol: int, is_highest: bool = False, prev_3d_max: int = 0):
        """Records 9:15 AM OHLC candle for a stock symbol."""
        today_str = datetime.date.today().isoformat()
        vwap = round((h + l + c) / 3.0, 2)
        candle_data = {
            "date": today_str,
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "candle_time": "09:15",
            "price_0915_O": round(o, 2),
            "price_0915_H": round(h, 2),
            "price_0915_L": round(l, 2),
            "price_0915_C": round(c, 2),
            "volume_0915": vol,
            "vwap": vwap,
            "is_highest": is_highest,
            "prev_3d_max": prev_3d_max
        }
        
        if timeframe == "5m":
            self.candles_5m[f"{today_str}|{symbol.upper()}"] = candle_data
            logger.info(f"[5M-CANDLE] {symbol} recorded: O={o} H={h} L={l} C={c} Vol={vol}")
        else:
            self.candles_15m[f"{today_str}|{symbol.upper()}"] = candle_data
            logger.info(f"[15M-CANDLE] {symbol} recorded: O={o} H={h} L={l} C={c} Vol={vol}")

    def persist_all(self):
        """Writes in-memory candle stores to disk JSON files."""
        today_str = datetime.date.today().isoformat()
        now_iso = datetime.datetime.now().isoformat()

        # 15m file
        payload_15m = {
            "__meta__": {
                "timeframe": "15m",
                "candle_time": "09:15",
                "date": today_str,
                "updated_at": now_iso,
                "stock_count": len(self.candles_15m)
            }
        }
        payload_15m.update(self.candles_15m)
        with open(CANDLE_15M_FILE, "w", encoding="utf-8") as f:
            json.dump(payload_15m, f, indent=2)

        # 5m file
        payload_5m = {
            "__meta__": {
                "timeframe": "5m",
                "candle_time": "09:15",
                "date": today_str,
                "updated_at": now_iso,
                "stock_count": len(self.candles_5m)
            }
        }
        payload_5m.update(self.candles_5m)
        with open(CANDLE_5M_FILE, "w", encoding="utf-8") as f:
            json.dump(payload_5m, f, indent=2)

        logger.info(f"[PERSIST] Saved {len(self.candles_15m)} 15m candles & {len(self.candles_5m)} 5m candles to JSON files.")

def start_candle_recorder():
    logger.info("Initializing Advance ORB 9:15 AM Candle Recorder service...")
    recorder = CandleRecorder()
    recorder.persist_all()
    logger.info("Candle Recorder service active and listening.")

if __name__ == '__main__':
    start_candle_recorder()

