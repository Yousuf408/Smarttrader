# ==============================================================================
# ANGEL ONE HOLDINGS & FUNDS MODULE
#
# Fetches fund balance, holdings, and open positions from Angel One APIs
# using the SmartConnect SDK or raw REST calls.
# ==============================================================================

import requests
from .angel_margin_calculator import (
    get_access_token,
    _CREDS,
    is_connected,
    _SMART_API,
    _make_smart_connect,
    ANGEL_PROXIES,
)

ANGEL_BASE_URL = "https://apiconnect.angelone.in"
ANGEL_FUNDS_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/user/v1/getRMS"
ANGEL_HOLDINGS_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/portfolio/v1/getHolding"
ANGEL_POSITIONS_URL = f"{ANGEL_BASE_URL}/rest/secure/angelbroking/order/v1/getPosition"


def _make_headers():
    """Build Angel One API headers with SDK-style auth."""
    api_key = _CREDS.get("api_key", "")
    token = get_access_token()
    hdrs = {
        "X-PrivateKey": api_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-UserType": "USER",
        "X-SourceID": "WEB",
    }
    if _SMART_API is not None:
        try:
            hdrs["X-ClientLocalIP"] = _SMART_API.clientLocalIP
            hdrs["X-ClientPublicIP"] = _SMART_API.clientPublicIP
            hdrs["X-MACaddress"] = _SMART_API.clientMacAddress
        except AttributeError:
            hdrs["X-MACaddress"] = "46:e8:da:66:2a:fa"
    else:
        hdrs["X-MACaddress"] = "46:e8:da:66:2a:fa"
    return hdrs


def get_angel_fund_limit() -> dict:
    """Fetch RMS fund limit from Angel One.

    Returns:
        dict with keys:
            success: bool
            data: dict with fund details (or None on failure)
            error: str | None
    """
    if not is_connected():
        return {"success": False, "data": None, "error": "Angel One not connected"}
    try:
        headers = _make_headers()
        client_id = _CREDS.get("client_id", "")
        payload = {"clientcode": client_id}
        resp = requests.post(
            ANGEL_FUNDS_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "data": None,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        if data.get("status"):
            return {"success": True, "data": data.get("data"), "error": None}
        return {
            "success": False,
            "data": None,
            "error": data.get("message", "Angel RMS fetch failed"),
        }
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


def get_angel_holdings() -> dict:
    """Fetch current holdings from Angel One.

    Returns:
        dict with keys:
            success: bool
            data: list of holding dicts (or None on failure)
            error: str | None
    """
    if not is_connected():
        return {"success": False, "data": None, "error": "Angel One not connected"}
    try:
        headers = _make_headers()
        client_id = _CREDS.get("client_id", "")
        payload = {"clientcode": client_id}
        resp = requests.post(
            ANGEL_HOLDINGS_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "data": None,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        if data.get("status"):
            holdings = data.get("data", []) if isinstance(data.get("data"), list) else []
            return {"success": True, "data": holdings, "error": None}
        return {
            "success": False,
            "data": None,
            "error": data.get("message", "Angel holdings fetch failed"),
        }
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}


def get_angel_positions() -> dict:
    """Fetch open positions from Angel One.

    Returns:
        dict with keys:
            success: bool
            data: list of position dicts (or None on failure)
            error: str | None
    """
    if not is_connected():
        return {"success": False, "data": None, "error": "Angel One not connected"}
    try:
        headers = _make_headers()
        client_id = _CREDS.get("client_id", "")
        payload = {"clientcode": client_id}
        resp = requests.post(
            ANGEL_POSITIONS_URL,
            json=payload,
            headers=headers,
            timeout=10,
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "data": None,
                "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            }
        data = resp.json()
        if data.get("status"):
            positions = data.get("data", []) if isinstance(data.get("data"), list) else []
            return {"success": True, "data": positions, "error": None}
        return {
            "success": False,
            "data": None,
            "error": data.get("message", "Angel positions fetch failed"),
        }
    except Exception as e:
        return {"success": False, "data": None, "error": str(e)}
