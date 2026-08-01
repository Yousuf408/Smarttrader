"""
Big Players Strategy Module

Concept:
  A stock qualifies as a "Big Players" candidate when its price touches
  or breaks below the 09:15 IST opening-candle LOW intraday, and then
  recovers back above that level. This signals institutional accumulation
  (big players stepping in at support).

Breakout status:
  - "Active"  → price touched/broke the 09:15 low, then recovered above it
  - "Waiting" → pattern not yet confirmed

Support price = the 09:15 opening-candle low.

Data source: CandleTracker (WebSocket-built candles), NOT yfinance.
Using yfinance here was the reason for 0% diff / inaccurate high-low
values — now we rely solely on live Angel One WebSocket data.
"""

from typing import Optional


class BigPlayersStrategy:
    """Identifies support-then-reversal patterns using candle_tracker data."""

    def __init__(self):
        pass

    def calculate_breakout_status(self, row: dict) -> str:
        """
        Return 'Active' if price touched/broke the 09:15 low intraday
        and then recovered above it; otherwise return 'Waiting'.

        Uses only candle_tracker data (WebSocket-built) — NO yfinance calls.

        Expects row keys: Symbol, low915, Price (current price), todayLow.
        If todayLow is present and < low915, and current price > low915,
        we consider the breakout confirmed.
        """
        low915 = row.get("low915")
        current_price = row.get("Price")
        today_low = row.get("todayLow")

        if low915 is None or current_price is None:
            return "Waiting"

        try:
            low915 = float(low915)
            current_price = float(current_price)
        except (TypeError, ValueError):
            return "Waiting"

        # If price hasn't recovered above the 09:15 low, definitely Waiting.
        if current_price <= low915:
            return "Waiting"

        # Check if price dipped below the 09:15 low intraday using
        # candle_tracker's day_low (WebSocket data, NOT yfinance).
        if today_low is None:
            return "Waiting"
        try:
            today_low = float(today_low)
        except (TypeError, ValueError):
            return "Waiting"

        if today_low >= low915:
            # Never touched/broke support.
            return "Waiting"

        # Price dipped below 09:15 low AND has now recovered above it.
        return "Active"

    def calculate_support_price(self, row: dict) -> Optional[float]:
        """The 09:15 opening-candle low is the support price."""
        low = row.get("low915")
        if low is not None:
            try:
                return round(float(low), 2)
            except (TypeError, ValueError):
                pass
        return None


# -------------------------------------------------------------------
# Standalone convenience wrappers (importable directly from strategy)
# -------------------------------------------------------------------
def calculate_breakout_status(row: dict) -> str:
    return BigPlayersStrategy().calculate_breakout_status(row)


def calculate_support_price(row: dict) -> Optional[float]:
    return BigPlayersStrategy().calculate_support_price(row)
