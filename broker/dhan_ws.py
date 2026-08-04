# ================================================================================
# DHANHQ LIVE MARKET FEED (WebSocket v2) — tick-by-tick equity quotes
# ================================================================================
# Connects to Dhan's market feed WebSocket and streams tick-by-tick data for
# every watchlist stock (up to 5000 instruments per connection).  Dhan's feed
# requires the "DhanHQ Data API" subscription (₹499/mo, see Dhan support) —
# without it the server rejects the connection and this module stays idle so
# the Angel One WebSocket remains the live source (automatic fallback —
# see advance_orb.common._build_ticks_by_symbol()).
#
# Wire format (v2):
#   url      : wss://api-feed.dhan.co?version=2&token=<access_token>&clientId=<cid>&authType=2
#   subscribe: JSON {"RequestCode": 17, "InstrumentCount": N,
#                    "InstrumentList": [{"ExchangeSegment": "NSE_EQ",
#                                        "SecurityId": "1234"}, ...]}  (batches of 100)
#   responses: Binary.  First byte = packet type:
#                  2 = Ticker      (B H B I f I)
#                  3 = MarketDepth (ignored here)
#                  4 = Quote       (B H B I f H I f I I I f f f f)
#                  6 = Prev Close  (B H B I f I)
#                  7 = Status      (ignored)
#                  8 = Full        (ignored here — Quote covers OHLCV)
#                  50 = Disconnect message
# ================================================================================

import json
import logging
import struct
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    from websockets.sync.client import connect as _ws_connect
except Exception:  # pragma: no cover
    _ws_connect = None

try:
    from broker.quantity_calculator import (
        get_security_id,
        renew_dhan_access_token,
        _cred as _dhan_cred,
        DHAN_PROXY_URL,
        _DHAN_CREDS,
    )
    from server.candle_tracker import candle_tracker
except Exception:  # pragma: no cover
    get_security_id = None
    renew_dhan_access_token = None
    _dhan_cred = lambda k: ""
    DHAN_PROXY_URL = ""
    _DHAN_CREDS = {}
    candle_tracker = None

IST = ZoneInfo("Asia/Kolkata")
logger = logging.getLogger("dhan_ws")

FEED_WSS = "wss://api-feed.dhan.co"
REQUEST_QUOTE = 17          # subscribe Quote packets (ticker + OHLCV + prev close)

# ---------------------------------------------------------------------------
# In-memory tick store — mirrors broker/angel_ws.latest_ticks shape.
#   latest_ticks[security_id] = {ltp, open, high, low, close, volume,
#                                change_pct, symbol, token, timestamp}
# ---------------------------------------------------------------------------
latest_ticks: dict[str, dict] = {}
_symbol_by_secid: dict[str, str] = {}   # security_id -> base symbol ("ITC")
_prev_close: dict[str, float] = {}      # security_id -> yesterday close
_ACTIVE_SPECS: list[tuple[str, str]] = []  # [(base_symbol, security_id)] — resolved once

_stop = threading.Event()
_thread: threading.Thread | None = None
_connected = False
_running = False

# ---------------------------------------------------------------------------
# Binary packet parsing (DhanHQ v2 Live Market Feed)
# ---------------------------------------------------------------------------

def _utc_epoch_to_ist(epoch: int) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), IST).strftime("%H:%M:%S")
    except Exception:
        return datetime.now(IST).strftime("%H:%M:%S")


def _parse_packet(data: bytes) -> None:
    """Parse one binary feed frame and merge it into latest_ticks."""
    if not data:
        return
    try:
        ptype = struct.unpack('<B', data[0:1])[0]
    except Exception:
        return

    try:
        if ptype == 2:      # Ticker — B H B I f I
            h = struct.unpack('<BHBIfI', data[0:16])
            _apply_ltp(str(h[3]), h[4], int(h[5]))
        elif ptype == 4:    # Quote — B H B I f H I f I I I f f f f
            h = struct.unpack('<BHBIfHIfIIIffff', data[0:50])
            secid = str(h[3])
            _apply_quote(
                secid,
                ltp=h[4],
                volume=int(h[8]),
                open_price=h[11],
                close_price=h[12],
                high=h[13],
                low=h[14],
                ltt_epoch=int(h[6]),
            )
        elif ptype == 6:    # Prev Close — B H B I f I
            h = struct.unpack('<BHBIfI', data[0:16])
            _prev_close[str(h[3])] = float(h[4])
            _update_change_pct(str(h[3]))
    except Exception:
        pass


def _apply_ltp(secid: str, ltp: float, ltt_epoch: int) -> None:
    entry = latest_ticks.setdefault(secid, {})
    entry["ltp"] = float(ltp)
    entry["timestamp"] = _utc_epoch_to_ist(ltt_epoch)
    _update_change_pct(secid)


def _apply_quote(secid: str, ltp, volume, open_price, close_price,
                 high, low, ltt_epoch: int) -> None:
    sym = _symbol_by_secid.get(secid, "")
    entry = latest_ticks.setdefault(secid, {})
    entry.update({
        "ltp": float(ltp) if ltp and ltp > 0 else entry.get("ltp", 0),
        "open": float(open_price) if open_price and open_price > 0 else entry.get("open", 0),
        "high": float(high) if high and high > 0 else entry.get("high", 0),
        "low": float(low) if low and low > 0 else entry.get("low", 0),
        # Dhan's quote "close" for cash equities is yesterday's close.
        "close": float(close_price) if close_price and close_price > 0 else entry.get("close", 0),
        "volume": int(volume) if volume >= 0 else entry.get("volume", 0),
        "symbol": f"{sym}-EQ" if sym else entry.get("symbol", ""),
        "token": secid,
    })
    entry["timestamp"] = _utc_epoch_to_ist(ltt_epoch)
    if secid not in _prev_close and close_price and close_price > 0:
        _prev_close[secid] = float(close_price)
    _update_change_pct(secid)


def _update_change_pct(secid: str) -> None:
    entry = latest_ticks.get(secid)
    if not entry:
        return
    ltp = entry.get("ltp") or 0
    pc = _prev_close.get(secid) or 0
    if pc and pc > 0 and ltp > 0:
        entry["change_pct"] = round((ltp - pc) / pc * 100, 4)
    else:
        entry.setdefault("change_pct", 0)

# ---------------------------------------------------------------------------
# Connection loop (daemon thread, auto-reconnect with backoff)
# ---------------------------------------------------------------------------

def _build_watchlist_specs(watchlist) -> list[tuple[str, str]]:
    """Return [(base_symbol, security_id)] for watchlist stocks."""
    specs: list[tuple[str, str]] = []
    if watchlist:
        items = watchlist
    elif candle_tracker is not None and hasattr(candle_tracker, "token_by_symbol"):
        items = [(sym, 0, "stock") for sym in candle_tracker.token_by_symbol.keys()]
    else:
        items = []

    seen = set()
    for item in items:
        name = item[0] if isinstance(item, (tuple, list)) and item else item
        kind = item[2] if isinstance(item, (tuple, list)) and len(item) > 2 else "stock"
        if kind not in ("stock", "equity"):
            continue
        base = str(name).strip().upper()
        if not base or base in seen:
            continue
        if get_security_id is None:
            continue
        try:
            secid = get_security_id(base)
        except Exception:
            secid = None
        if secid:
            seen.add(base)
            specs.append((base, str(secid)))
    return specs


def _subscribe(ws, specs: list[tuple[str, str]]) -> None:
    """Send Quote subscription for all instruments, in batches of 100."""
    for i in range(0, len(specs), 100):
        batch = specs[i:i + 100]
        message = {
            "RequestCode": REQUEST_QUOTE,
            "InstrumentCount": len(batch),
            "InstrumentList": [
                {"ExchangeSegment": "NSE_EQ", "SecurityId": sid}
                for _, sid in batch
            ],
        }
        ws.send(json.dumps(message))


def _connect_and_stream() -> None:
    global _connected
    if _ws_connect is None:
        logger.warning("⚠️ Dhan feed unavailable: websockets library not installed")
        return

    cid = str(_dhan_cred("client_id") or "")
    tok = str(_dhan_cred("access_token") or "")
    if not cid or not tok:
        logger.info("ℹ️ Dhan feed idle — Dhan broker not connected (Angel WS stays primary)")
        return

    # Renew if the access token is about to expire and then (re)try connect.
    if renew_dhan_access_token is not None:
        try:
            issued = _DHAN_CREDS.get("token_issued_at")
            if issued and (time.time() - float(issued)) > 20 * 3600:
                renew_dhan_access_token()
                tok = str(_dhan_cred("access_token") or "")
        except Exception:
            pass

    url = f"{FEED_WSS}?version=2&token={tok}&clientId={cid}&authType=2"
    backoff = 1.0
    while not _stop.is_set():
        ws = None
        try:
            kwargs = {"proxy": DHAN_PROXY_URL} if DHAN_PROXY_URL else {}
            ws = _ws_connect(url, open_timeout=20, **kwargs)
            specs = _ACTIVE_SPECS or _build_watchlist_specs(None)
            _symbol_by_secid.clear()
            _symbol_by_secid.update({sid: sym for sym, sid in specs})
            if not specs:
                logger.warning("⚠️ Dhan feed: no resolvable instruments to subscribe")
                _stop.set()
                return
            _subscribe(ws, specs)
            _connected = True
            backoff = 1.0
            logger.info(f"✅ Dhan feed connected — subscribed {len(specs)} stocks")

            for raw in ws:
                if _stop.is_set():
                    break
                _parse_packet(raw)
        except Exception as e:
            msg = str(e)
            if "806" in msg or "subscription" in msg.lower() or "unauthorized" in msg.lower():
                logger.warning(
                    "ℹ️ Dhan feed rejected (need DhanHQ Data API subscription, ₹499/mo) — "
                    "Angel One WS remains primary: %s", msg[:120]
                )
                _stop.set()
                return
            logger.warning(f"⚠️ Dhan feed error — retrying in {backoff:.0f}s: {msg[:150]}")
        finally:
            _connected = False
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            if not _stop.is_set():
                _stop.wait(min(backoff, 60))
                backoff = min(backoff * 2, 60)


def _runner() -> None:
    while not _stop.is_set():
        try:
            _connect_and_stream()
        except Exception:
            logger.debug("Dhan feed runner exception", exc_info=True)
        if not _stop.is_set():
            _stop.wait(5)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def start_dhan_ws(watchlist=None):
    """Start the Dhan market feed in a daemon thread. Idles gracefully when
    Dhan creds are missing / subscription absent — Angel WS stays live.

    Args:
        watchlist: optional list of (name, token, kind) — same shape as the
                   Angel WS watchlist. Defaults to candle_tracker stocks.
    Returns:
        dict: {"success": bool, "error": str | None, "subscribed": int}
    """
    global _thread, _running
    if _running and _thread and _thread.is_alive():
        return {"success": True, "message": "Already running", "subscribed": len(_symbol_by_secid)}

    # Warm the security-id map up-front so failures are visible immediately.
    specs = _ACTIVE_SPECS or _build_watchlist_specs(watchlist)
    if not specs:
        return {
            "success": False,
            "error": "No resolvable Dhan instruments"
            " (feed needs DhanHQ Data API subscription)",
            "subscribed": 0,
        }
    if _dhan_cred and not (_dhan_cred("client_id") and _dhan_cred("access_token")):
        return {"success": False, "error": "Dhan broker not connected", "subscribed": 0}

    _ACTIVE_SPECS[:] = specs
    _stop.clear()
    _running = True
    _thread = threading.Thread(target=_runner, daemon=True, name="dhan-feed")
    _thread.start()
    logger.info(f"🚀 Dhan feed starting for {len(specs)} stocks")
    return {"success": True, "error": None, "subscribed": len(specs)}


def stop_dhan_ws() -> None:
    """Stop the Dhan feed thread."""
    global _running
    _stop.set()
    _running = False
    if _thread and _thread.is_alive():
        _thread.join(timeout=5)


def is_ws_connected() -> bool:
    """True when the Dhan feed socket is live."""
    return _connected


def get_latest_ticks() -> dict:
    """Return all Dhan ticks keyed by security_id."""
    return latest_ticks


def get_ticks_by_symbol() -> dict:
    """Return Dhan ticks keyed by base symbol (e.g. 'ITC')."""
    out = {}
    for secid, data in latest_ticks.items():
        sym = _symbol_by_secid.get(secid)
        if sym:
            out[sym] = data
    return out


def get_subscription_status() -> dict:
    return {
        "connected": _connected,
        "subscribed": len(_symbol_by_secid),
        "ticks_received": len(latest_ticks),
    }
