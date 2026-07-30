# ==============================================================================
# ANGEL ONE WATCHLIST EXPORT
#
# Manages watchlists via Angel One's REST API — get, create, and add symbols.
# Routes requests through the SmartConnect SDK's session to avoid the WAF
# blocking that raw requests.get/post triggers.
# ==============================================================================

import json
from broker.angel_margin_calculator import (
    _SMART_API,
    _CREDS,
    get_access_token,
    resolve_symbol_token,
)

WATCHLIST_BASE = "https://apiconnect.angelone.in/rest/secure/angelbroking/watchlist/v1"
WATCHLIST_NAME = "TradeAlgo Pro"


def _sdk_request(method, path, json_body=None):
    """Make an HTTP request through the SmartConnect SDK's session.

    Uses ``_SMART_API.reqsession`` (a ``requests.Session`` with the correct TLS
    config, client-identification headers, and proxy) plus the Authorization
    token, so Angel One's WAF doesn't reject the call.

    Args:
        method (str): ``"GET"`` or ``"POST"``.
        path (str): Full URL path.
        json_body (dict|None): JSON body for POST requests.

    Returns:
        requests.Response or None if the SDK isn't available.
    """
    if _SMART_API is None:
        return None

    headers = _SMART_API.requestHeaders()
    token = get_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    session = _SMART_API.reqsession
    if method.upper() == "GET":
        return session.get(path, headers=headers, timeout=15)
    else:
        return session.post(path, json=json_body, headers=headers, timeout=15)


def get_watchlists():
    """Fetch all watchlists from the user's Angel One account."""
    api_key = _CREDS.get("api_key", "")
    if not api_key or not get_access_token():
        return {"ok": False, "error": "Angel One not connected"}
    try:
        resp = _sdk_request("GET", f"{WATCHLIST_BASE}/getWatchlist")
        if resp is None:
            return {"ok": False, "error": "SmartConnect SDK not initialised"}
        if resp.status_code == 200:
            try:
                data = resp.json()
            except json.JSONDecodeError:
                return {"ok": False, "error": f"Angel One returned non-JSON: {resp.text[:200]}"}
            if data.get("status") and data.get("data"):
                return {"ok": True, "watchlists": data["data"]}
            return {"ok": True, "watchlists": []}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_watchlist(name):
    """Create a new watchlist on the user's Angel One account."""
    api_key = _CREDS.get("api_key", "")
    if not api_key or not get_access_token():
        return {"ok": False, "error": "Angel One not connected"}
    try:
        resp = _sdk_request("POST", f"{WATCHLIST_BASE}/createWatchlist", {"wlname": name})
        if resp is None:
            return {"ok": False, "error": "SmartConnect SDK not initialised"}
        if resp.status_code in (200, 201):
            try:
                data = resp.json()
            except json.JSONDecodeError:
                return {"ok": False, "error": f"Angel One returned non-JSON: {resp.text[:200]}"}
            if data.get("status"):
                return {"ok": True}
            return {"ok": False, "error": data.get("message", "Create failed")}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def add_symbols_to_watchlist(watchlist_name, symbols):
    """Add a list of stock symbols to an Angel One watchlist.

    Each symbol is resolved to its Angel One token (e.g. RELIANCE → RELIANCE-EQ / 2885).
    Batches in chunks of 50 per the API limit.
    """
    api_key = _CREDS.get("api_key", "")
    if not api_key or not get_access_token():
        return {"ok": False, "error": "Angel One not connected", "added": 0, "failed": list(symbols)}

    # Resolve each symbol to its Angel One token + exchange
    resolved = []
    failed = []
    for sym in symbols:
        tradingsymbol, token_id = resolve_symbol_token(sym, "NSE")
        if tradingsymbol and token_id:
            resolved.append({
                "tradingsymbol": tradingsymbol,
                "symboltoken": str(token_id),
                "exchange": "NSE",
            })
        else:
            # Try BSE as fallback
            tradingsymbol, token_id = resolve_symbol_token(sym, "BSE")
            if tradingsymbol and token_id:
                resolved.append({
                    "tradingsymbol": tradingsymbol,
                    "symboltoken": str(token_id),
                    "exchange": "BSE",
                })
            else:
                failed.append(sym)

    if not resolved:
        return {
            "ok": False,
            "error": "Could not resolve any symbols to Angel One tokens",
            "added": 0,
            "failed": failed,
        }

    # Angel One's addToWatchlist accepts up to 50 symbols per call
    total_added = 0
    batch_errors = []
    for i in range(0, len(resolved), 50):
        batch = resolved[i : i + 50]
        try:
            resp = _sdk_request(
                "POST",
                f"{WATCHLIST_BASE}/addToWatchlist",
                {"wlname": watchlist_name, "symbols": batch},
            )
            if resp is None:
                batch_errors.append(f"batch {i//50}: SDK not initialised")
                continue
            if resp.status_code in (200, 201):
                try:
                    data = resp.json()
                except json.JSONDecodeError:
                    batch_errors.append(f"batch {i//50}: non-JSON response: {resp.text[:100]}")
                    continue
                if data.get("status"):
                    total_added += len(batch)
                else:
                    batch_errors.append(f"batch {i//50}: {data.get('message', 'unknown')}")
            else:
                batch_errors.append(f"batch {i//50}: HTTP {resp.status_code}")
        except Exception as e:
            batch_errors.append(f"batch {i//50}: {e}")

    return {
        "ok": total_added > 0,
        "added": total_added,
        "failed": failed + (batch_errors if total_added == 0 else []),
        "watchlist": watchlist_name,
        "error": "; ".join(batch_errors) if batch_errors and total_added == 0 else None,
    }


def export_symbols(symbols):
    """High-level helper: get or create 'TradeAlgo Pro' watchlist, then add symbols."""
    if not _CREDS.get("api_key") or not get_access_token():
        return {"ok": False, "error": "Angel One not connected"}

    # Step 1: Get existing watchlists
    wl_result = get_watchlists()
    if not wl_result.get("ok"):
        return wl_result

    watchlists = wl_result.get("watchlists", [])
    exists = any(
        w.get("wlname", "").strip().lower() == WATCHLIST_NAME.lower()
        for w in watchlists
    )

    # Step 2: Create if not found
    if not exists:
        create_result = create_watchlist(WATCHLIST_NAME)
        if not create_result.get("ok"):
            return create_result

    # Step 3: Add symbols
    return add_symbols_to_watchlist(WATCHLIST_NAME, symbols)
