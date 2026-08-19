"""
Big Players Main Execution and Scanning Logic
"""
import math
import logging

logger = logging.getLogger(__name__)

class BigPlayersDetector:
    def __init__(self, threshold_relvol=2.5, min_turnover_cr=5.0):
        self.threshold_relvol = threshold_relvol
        self.min_turnover_cr = min_turnover_cr

    def analyze_stock(self, symbol, current_volume, avg_volume_5d, price):
        if not avg_volume_5d or avg_volume_5d == 0:
            return None
        rel_vol = current_volume / avg_volume_5d
        turnover_cr = (current_volume * price) / 10000000.0
        
        is_active = (rel_vol >= self.threshold_relvol) and (turnover_cr >= self.min_turnover_cr)
        return {
            "symbol": symbol,
            "rel_vol": round(rel_vol, 2),
            "turnover_cr": round(turnover_cr, 2),
            "big_player_active": is_active
        }
