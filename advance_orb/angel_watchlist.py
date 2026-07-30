# ==============================================================================
# ANGEL ONE WATCHLIST EXPORT
#
# Manages watchlists via Angel One's REST API — get, create, and add symbols.
# Uses the same auth headers and proxy as the order placement module.
# ==============================================================================

import requests
from broker.angel_orders import (
    _make_sdk_headers,
    ANGEL_PROXIES,
)
from broker.angel_margin_calculator import (
    get_access_token,
    _CREDS,
    resolve_symbol_token,
)

WATCHLIST_BASE = "https://apiconnect.angelone.in/rest/secure/angelbroking/watchlist/v1"
WATCHLIST_NAME = "TradeAlgo Pro"


def get_watchlists():
    """Fetch all watchlists from the user's Angel One account.

    Returns:
        list of dict: [{wlname: str, ...}] or empty list on failure.
    """
    api_key = _CREDS.get("api_key", "")
    token = get_access_token()
    if not api_key or not token:
        return {"ok": False, "error": "Angel One not connected"}
    try:
        resp = requests.get(
            f"{WATCHLIST_BASE}/getWatchlist",
            headers=_make_sdk_headers(api_key, token),
            proxies=ANGEL_PROXIES,
            timeout=10,
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") and data.get("data"):
                return {"ok": True, "watchlists": data["data"]}
            return {"ok": True, "watchlists": []}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_watchlist(name):
    """Create a new watchlist on the user's Angel One account.

    Args:
        name (str): Watchlist name.

    Returns:
        dict: {"ok": bool, "error": str|None}
    """
    api_key = _CREDS.get("api_key", "")
    token = get_access_token()
    if not api_key or not token:
        return {"ok": False, "error": "Angel One not connected"}
    try:
        resp = requests.post(
            f"{WATCHLIST_BASE}/createWatchlist",
            json={"wlname": name},
            headers=_make_sdk_headers(api_key, token),
            proxies=ANGEL_PROXIES,
            timeout=10,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            if data.get("status"):
                return {"ok": True}
            return {"ok": False, "error": data.get("message", "Create failed")}
        return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def add_symbols_to_watchlist(watchlist_name, symbols):
    """Add a list of stock symbols to an Angel One watchlist.

    Each symbol is resolved to its Angel One token (e.g. RELIANCE → RELIANCE-EQ / 2885).

    Args:
        watchlist_name (str): Name of the target watchlist.
        symbols (list of str): Stock symbols like ["RELIANCE", "TCS", "INFY"].

    Returns:
        dict: {"ok": bool, "added": int, "failed": list, "error": str|None}
    """
    api_key = _CREDS.get("api_key", "")
    token = get_access_token()
    if not api_key or not token:
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
    # Batch in chunks of 50
    total_added = 0
    batch_errors = []
    for i in range(0, len(resolved), 50):
        batch = resolved[i : i + 50]
        try:
            resp = requests.post(
                f"{WATCHLIST_BASE}/addToWatchlist",
                json={"wlname": watchlist_name, "symbols": batch},
                headers=_make_sdk_headers(api_key, token),
                proxies=ANGEL_PROXIES,
                timeout=10,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
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
    """High-level helper: get or create 'TradeAlgo Pro' watchlist, then add symbols.

    Args:
        symbols (list of str): Stock symbols to add.

    Returns:
        dict: Result from add_symbols_to_watchlist or error.
    """
    api_key = _CREDS.get("api_key", "")
    token = get_access_token()
    if not api_key or not token:
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
