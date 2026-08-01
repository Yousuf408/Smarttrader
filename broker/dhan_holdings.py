# ==============================================================================
# DHAN HOLDINGS & FUNDS MODULE
#
# Fetches fund balance, holdings, and open positions from DhanHQ APIs.
# Routes through the static-IP proxy (same as orders).
# ==============================================================================

import requests
from .quantity_calculator import (
    DHAN_ACCESS_TOKEN,
    DHAN_CLIENT_ID,
    DHAN_PROXIES,
)

DHAN_FUND_LIMIT_URL = "https://api.dhan.co/v2/fundlimit"
DHAN_HOLDINGS_URL = "https://api.dhan.co/v2/holdings"
DHAN_POSITIONS_URL = "https://api.dhan.co/v2/positions"


def get_dhan_fund_limit() -> dict:
    """Fetch fund limit / available balance from Dhan.

    Returns:
        dict with keys:
            success: bool
            data: dict with fund details (or None on failure)
            error: str | None
    """
    token = str(DHAN_ACCESS_TOKEN or "").strip()
    if not token or token == "your_dhan_access_token_here":
        return {"success": False, "data": None, "error": "Dhan not connected"}
    client_id = str(DHAN_CLIENT_ID or "").strip()
    headers = {"access-token": token, "Content-Type": "application/json"}
    try:
        resp = requests.get(
            DHAN_FUND_LIMIT_URL,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "data": None,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        return {"success": True, "data": data, "error": None}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


def get_dhan_holdings() -> dict:
    """Fetch current holdings from Dhan.

    Returns:
        dict with keys:
            success: bool
            data: list of holding dicts (or None on failure)
            error: str | None
    """
    token = str(DHAN_ACCESS_TOKEN or "").strip()
    if not token or token == "your_dhan_access_token_here":
        return {"success": False, "data": None, "error": "Dhan not connected"}
    headers = {"access-token": token, "Content-Type": "application/json"}
    try:
        resp = requests.get(
            DHAN_HOLDINGS_URL,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "data": None,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        holdings = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        return {"success": True, "data": holdings, "error": None}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


def get_dhan_positions() -> dict:
    """Fetch open positions from Dhan.

    Returns:
        dict with keys:
            success: bool
            data: list of position dicts (or None on failure)
            error: str | None
    """
    token = str(DHAN_ACCESS_TOKEN or "").strip()
    if not token or token == "your_dhan_access_token_here":
        return {"success": False, "data": None, "error": "Dhan not connected"}
    headers = {"access-token": token, "Content-Type": "application/json"}
    try:
        resp = requests.get(
            DHAN_POSITIONS_URL,
            headers=headers,
            proxies=DHAN_PROXIES,
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "data": None,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        positions = data if isinstance(data, list) else data.get("data", []) if isinstance(data, dict) else []
        return {"success": True, "data": positions, "error": None}
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}
