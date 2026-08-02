# ==============================================================================
# ANGEL ONE HOLDINGS & FUNDS MODULE
#
# Fetches fund balance, holdings, and open positions from Angel One APIs
# using the SmartConnect SDK — raw requests.post() triggers WAF blocks.
# ==============================================================================

from .angel_margin_calculator import (
    is_connected,
    _SMART_API,
)


def get_angel_fund_limit() -> dict:
    """Fetch RMS fund limit from Angel One via SmartConnect SDK.

    Returns:
        dict with keys:
            success: bool
            data: dict with fund details (or None on failure)
            error: str | None
    """
    if not is_connected():
        return {"success": False, "data": None, "error": "Angel One not connected"}
    if _SMART_API is None:
        return {"success": False, "data": None, "error": "SmartConnect SDK not initialised"}
    try:
        resp = _SMART_API.rmsLimit()
        if resp is None:
            return {"success": False, "data": None, "error": "SDK returned None"}
        if resp.get("status"):
            return {"success": True, "data": resp.get("data"), "error": None}
        return {
            "success": False,
            "data": None,
            "error": resp.get("message", "Angel RMS fetch failed"),
        }
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


def get_angel_holdings() -> dict:
    """Fetch current holdings from Angel One via SmartConnect SDK.

    Returns:
        dict with keys:
            success: bool
            data: list of holding dicts (or [] on failure)
            error: str | None
    """
    if not is_connected():
        return {"success": False, "data": None, "error": "Angel One not connected"}
    if _SMART_API is None:
        return {"success": False, "data": None, "error": "SmartConnect SDK not initialised"}
    try:
        resp = _SMART_API.holding()
        if resp is None:
            return {"success": False, "data": [], "error": "SDK returned None"}
        if resp.get("status"):
            holdings = resp.get("data", []) if isinstance(resp.get("data"), list) else []
            return {"success": True, "data": holdings, "error": None}
        return {
            "success": False,
            "data": [],
            "error": resp.get("message", "Angel holdings fetch failed"),
        }
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)}


def get_angel_positions() -> dict:
    """Fetch open positions from Angel One via SmartConnect SDK.

    Returns:
        dict with keys:
            success: bool
            data: list of position dicts (or [] on failure)
            error: str | None
    """
    if not is_connected():
        return {"success": False, "data": None, "error": "Angel One not connected"}
    if _SMART_API is None:
        return {"success": False, "data": None, "error": "SmartConnect SDK not initialised"}
    try:
        resp = _SMART_API.position()
        if resp is None:
            return {"success": False, "data": [], "error": "SDK returned None"}
        if resp.get("status"):
            positions = resp.get("data", []) if isinstance(resp.get("data"), list) else []
            return {"success": True, "data": positions, "error": None}
        return {
            "success": False,
            "data": [],
            "error": resp.get("message", "Angel positions fetch failed"),
        }
    except Exception as e:
        return {"success": False, "data": [], "error": str(e)}
