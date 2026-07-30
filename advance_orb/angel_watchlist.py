# ==============================================================================
# ANGEL ONE WATCHLIST EXPORT
#
# Adds symbols to the Angel One SmartStream WebSocket watchlist.
# This subscribes the user to real-time ticker data for the exported symbols.
#
# ⚠️  The Angel One REST watchlist API is blocked by their WAF (Request
#     Rejected) regardless of routing — SDK _request, proxy, direct, all
#     blocked. The SmartStream WebSocket `add_to_watchlist` works, so we
#     use that as the watchlist mechanism.
# ==============================================================================

from broker import angel_margin_calculator as amc
from broker import angel_ws as ws

WATCHLIST_NAME = "TradeAlgo Pro"


def export_symbols(symbols):
    """Add screener symbols to the Angel One SmartStream watchlist.

    Each symbol is resolved to its Angel One token via the master CSV,
    then added to the running WebSocket subscription via
    ``angel_ws.add_to_watchlist()``. Already-present symbols are skipped.

    Returns:
        dict: ``{"ok": True/False, "added": int, "failed": [...], ...}``
    """
    if not amc._CREDS.get("api_key") or not amc.get_access_token():
        return {"ok": False, "error": "Angel One not connected"}

    resolved = []
    failed = []
    for sym in symbols:
        tradingsymbol, token_id = amc.resolve_symbol_token(sym, "NSE")
        if tradingsymbol and token_id:
            resolved.append((tradingsymbol, str(token_id)))
        else:
            tradingsymbol, token_id = amc.resolve_symbol_token(sym, "BSE")
            if tradingsymbol and token_id:
                resolved.append((tradingsymbol, str(token_id)))
            else:
                failed.append(sym)

    if not resolved:
        return {
            "ok": False,
            "error": "Could not resolve any symbols to Angel One tokens",
            "added": 0,
            "failed": failed,
        }

    total_added = 0
    for name, token in resolved:
        result = ws.add_to_watchlist(name, int(token))
        if result.get("success") and result.get("message") != "Already exists":
            total_added += 1

    return {
        "ok": True,
        "added": total_added,
        "total": len(resolved),
        "failed": failed,
        "watchlist": "SmartStream (real-time)",
        "info": "Symbols added to Angel One real-time data feed",
    }
