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
"""

import yfinance as yf
import pandas as pd
from zoneinfo import ZoneInfo
from typing import Optional

IST = ZoneInfo("Asia/Kolkata")


class BigPlayersStrategy:
    """Identifies support-then-reversal patterns on 5-min candles."""

    def __init__(self):
        pass

    def calculate_breakout_status(self, row: dict) -> str:
        """
        Return 'Active' if price touched/broke the 09:15 low intraday
        and then recovered above it; otherwise return 'Waiting'.

        Expects row keys: Symbol, low915, Price (current price).
        """
        symbol = row.get("Symbol", "")
        low915 = row.get("low915")
        current_price = row.get("Price")

        if not symbol or low915 is None or current_price is None:
            return "Waiting"

        try:
            low915 = float(low915)
            current_price = float(current_price)
        except (TypeError, ValueError):
            return "Waiting"

        # If price hasn't recovered above the 09:15 low, definitely Waiting.
        if current_price <= low915:
            return "Waiting"

        # Fetch today's 5-min candles to check the intraday sequence.
        ticker = f"{str(symbol).strip().upper()}.NS"
        try:
            candles = yf.download(
                tickers=ticker,
                period="1d",
                interval="5m",
                progress=False,
                auto_adjust=False,
                prepost=False,
                threads=False,
            )
        except Exception:
            return "Waiting"

        if candles is None or candles.empty:
            return "Waiting"

        # Handle MultiIndex columns (yfinance quirk).
        if isinstance(candles.columns, pd.MultiIndex):
            try:
                candles = candles.xs(ticker, axis=1, level=-1)
            except (KeyError, IndexError):
                try:
                    candles = candles.xs(ticker, axis=1, level=0)
                except (KeyError, IndexError):
                    return "Waiting"

        if "Low" not in candles or "Close" not in candles:
            return "Waiting"

        # Convert to IST.
        local_index = pd.DatetimeIndex(candles.index)
        if local_index.tz is None:
            local_index = local_index.tz_localize("UTC").tz_convert(IST)
        else:
            local_index = local_index.tz_convert(IST)
        candles = candles.copy()
        candles.index = local_index

        # Candles strictly AFTER the 09:15 opening candle.
        after_open = candles[
            (candles.index.hour > 9)
            | ((candles.index.hour == 9) & (candles.index.minute > 15))
        ]
        if after_open.empty:
            return "Waiting"

        lows = pd.to_numeric(after_open["Low"], errors="coerce")
        below_support = lows[lows < low915]

        if below_support.empty:
            # Never touched support.
            return "Waiting"

        # Found at least one candle that dipped below the 09:15 low.
        # Now check if a later candle closed back above support.
        first_dip_idx = below_support.index[0]
        after_dip = after_open.loc[first_dip_idx:]

        closes = pd.to_numeric(after_dip["Close"], errors="coerce")
        above_support = closes[closes > low915]

        if above_support.empty:
            return "Waiting"

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
