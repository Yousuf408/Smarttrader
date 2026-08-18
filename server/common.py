"""
Global Shared Utilities for Trading Strategies
Contains market timings, IST clock, trading holiday/session helpers, and mathematical formulas.
No strategy-specific rules (like ORB or BigPlayers) are placed here.
"""
import os
import sys
import math
import logging
from datetime import datetime, time, timezone, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Indian Standard Time Zone (UTC+5:30)
IST_TZ = timezone(timedelta(hours=5, minutes=30))

# Market Hours Constants
MARKET_OPEN_TIME = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)
PRE_MARKET_OPEN = time(9, 0)
PRE_MARKET_CLOSE = time(9, 8)

def get_ist_now() -> datetime:
    """Returns current datetime in Indian Standard Time (IST)."""
    return datetime.now(IST_TZ)

def is_market_open() -> bool:
    """
    Checks if the NSE/BSE cash equity market is currently open.
    Monday-Friday, 9:15 AM to 3:30 PM IST.
    """
    now = get_ist_now()
    # Check weekday (0 = Monday, 4 = Friday)
    if now.weekday() >= 5:
        return False
    current_time = now.time()
    return MARKET_OPEN_TIME <= current_time <= MARKET_CLOSE_TIME

def is_pre_market() -> bool:
    """Checks if pre-market session (9:00 AM - 9:08 AM IST) is active."""
    now = get_ist_now()
    if now.weekday() >= 5:
        return False
    return PRE_MARKET_OPEN <= now.time() <= PRE_MARKET_CLOSE

def calculate_max_qty(budget: float, parts: int, price: float, leverage: float = 5.0) -> int:
    """
    Calculates quantity allocation with intraday margin leverage.
    Formula: floor((budget / parts * leverage) / price)
    """
    if not budget or not parts or not price or price <= 0:
        return 0
    part_budget = budget / parts
    return math.floor((part_budget * leverage) / price)

def calculate_risk_reward(entry: float, sl: float, target: float) -> Dict[str, float]:
    """Calculates risk, reward, and Risk-to-Reward ratio."""
    if not entry or not sl or not target:
        return {"risk": 0.0, "reward": 0.0, "ratio": 0.0}
    risk = abs(entry - sl)
    reward = abs(target - entry)
    ratio = round(reward / risk, 2) if risk > 0 else 0.0
    return {
        "risk": round(risk, 2),
        "reward": round(reward, 2),
        "ratio": ratio
    }
