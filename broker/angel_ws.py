# ==============================================================================
# ANGEL ONE WEBSOCKET - Standalone Version (No Streamlit)
# Integrates with angel_margin_calculator.py for auth
# ==============================================================================

# ── websocket-client namespace-package workaround ──────────────────
# The websocket-client 1.9.0 installs as a plain directory without an
# ``__init__.py``, which makes ``import websocket`` resolve to a *namespace
# package* — an empty module that does not carry ``WebSocketApp``.
# SmartWebSocketV2 does ``import websocket`` then calls
# ``websocket.WebSocketApp(...)``, which fails with ``AttributeError``.
#
# We fix this by patching the websocket namespace *before* importing the
# SmartApi SDK, so that when ``smartWebSocketV2.py`` runs its module-level
# ``import websocket`` it picks up our patched version.
import websocket as _ws_ns
from websocket._app import WebSocketApp as _WebSocketApp
_ws_ns.WebSocketApp = _WebSocketApp

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from logzero import logger
import threading
import time
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path

# Import from your existing modules
from .angel_margin_calculator import (
    _CREDS,
    get_access_token,
    get_feed_token,
    is_connected as is_api_connected,
    load_master,
    ANGEL_PROXIES
)

# CandleTracker — real-time 5-min OHLC from WebSocket ticks
from server.candle_tracker import candle_tracker

# ==============================================================================
# CONSTANTS
# ==============================================================================
IST = timezone(timedelta(hours=5, minutes=30))

SAVED_TICKS_PATH = Path(__file__).resolve().parent.parent / "stocks" / "latest_ticks.json"

# ==============================================================================
# GLOBAL STATE
# ==============================================================================
latest_ticks = {}          # {token: {ltp, volume, open, high, low, close, ...}}
_raw_messages = []
_sws = None
_thread = None
_connected = False
_correlation_id = "trading_app_live"

# ── Auto-reconnect state ──────────────────────────────────────────
_reconnect_active = False              # set True while a reconnect is in progress
_reconnect_stop = threading.Event()    # set to stop reconnection loop
_reconnect_max_attempts = 0            # 0 = unlimited
_reconnect_delay = 5.0                 # initial delay (seconds)
_reconnect_max_delay = 120.0           # cap at 2 minutes
_reconnect_multiplier = 2.0            # exponential backoff factor

# Reverse lookup: token → display symbol (populated by _build_token_map)
_TOKEN_SYMBOL_MAP = {}

# Watchlist from config (you'll define this)
WATCHLIST = [
    # Format: (name, token, kind)
    # ("NIFTY", 26000, "index"),
    # ("BANKNIFTY", 26009, "index"),
    # ("RELIANCE", 2885, "stock"),
    # ("TCS", 2950, "stock"),
    # ("INFY", 2945, "stock"),
]

# ==============================================================================
# TICK PERSISTENCE — last-known prices survive restarts / disconnects
# ==============================================================================

def _save_ticks():
    """Persist latest_ticks to disk so they survive server restarts."""
    global latest_ticks
    try:
        SAVED_TICKS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(SAVED_TICKS_PATH, "w") as f:
            json.dump(latest_ticks, f, indent=2)
    except Exception:
        pass

def _load_saved_ticks():
    """Load last-known ticks from disk. Populates latest_ticks with
    stale-but-better-than-nothing prices when the WS isn't connected."""
    global latest_ticks
    try:
        if SAVED_TICKS_PATH.exists() and SAVED_TICKS_PATH.stat().st_size > 100:
            with open(SAVED_TICKS_PATH) as f:
                saved = json.load(f)
            if saved:
                latest_ticks = saved
                logger.info(f"📂 Loaded {len(saved)} saved ticks from disk")
    except Exception:
        pass

def _seed_ticks_from_rest():
    """Fallback: fetch LTP + day OHLC for all WATCHLIST tokens via
    Angel One REST /quote API and populate latest_ticks.

    This is used when the WebSocket hasn't received ticks yet (market
    closed, slow connection, etc.) so the table has baseline prices to
    show — even if slightly stale.
    """
    global latest_ticks
    if not WATCHLIST:
        return

    try:
        from .angel_margin_calculator import _make_smart_connect
        from SmartApi import SmartConnect

        sc = _make_smart_connect()
        sc.setAccessToken(get_access_token())
        sc.setFeedToken(get_feed_token())
        sc.setUserId(_CREDS.get("client_id", ""))

        # Collect all stock tokens, batch into groups of 50 (API limit)
        stock_tokens = [str(t) for _, t, k in WATCHLIST if k == "stock"]
        batch_size = 50
        filled = 0

        for i in range(0, len(stock_tokens), batch_size):
            batch = stock_tokens[i:i + batch_size]
            try:
                resp = sc.getMarketData(2, {"NSE": batch})
                if not resp or resp.get("status") is False:
                    continue
                fetched = resp.get("data", {}).get("fetched", [])
                for item in fetched:
                    tok = str(item.get("symbolToken", ""))
                    if not tok:
                        continue
                    sym = _TOKEN_SYMBOL_MAP.get(tok, item.get("tradingSymbol", ""))
                    if str(item.get("ltp", 0)).replace(".", "").replace("-", "") == "0":
                        continue  # skip zero-LTP entries
                    latest_ticks[tok] = {
                        "ltp": item.get("ltp", 0),
                        "open": item.get("open", 0),
                        "high": item.get("high", 0),
                        "low": item.get("low", 0),
                        "close": item.get("close", 0),
                        "volume": item.get("volume", 0),
                        "change": item.get("netChange", 0),
                        "change_pct": item.get("percentChange", 0),
                        "symbol": sym,
                        "token": tok,
                        "timestamp": datetime.now(IST).strftime("%H:%M:%S"),
                    }
                    filled += 1
            except Exception as e:
                logger.debug(f"REST quote batch {i//batch_size} error: {e}")

        if filled:
            logger.info(f"📡 Seeded {filled}/{len(stock_tokens)} ticks from REST API")
            _save_ticks()
    except Exception as e:
        logger.debug(f"REST tick seed error: {e}")

# ==============================================================================
# MARKET HOURS CHECK
# ==============================================================================

def is_market_open() -> bool:
    """Check if NSE market is open (9:15 AM - 3:45 PM IST, Mon-Fri)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= mins < (15 * 60 + 45)

# ==============================================================================
# WEBSOCKET CALLBACKS
# ==============================================================================

def on_data(wsapp, message):
    """Called on every tick from Angel One WebSocket."""
    global latest_ticks, _raw_messages

    try:
        logger.debug(f"📩 on_data received: type={type(message).__name__}")

        # Store raw message for debugging (last 5 only)
        _raw_messages.append(message)
        if len(_raw_messages) > 5:
            _raw_messages.pop(0)

        token = str(message.get('token', ''))
        if not token and isinstance(message, dict):
            # Try alternate key names
            token = str(message.get('tk', '') or message.get('symbolToken', '') or '')
        if not token:
            logger.debug("on_data — no token in message, skipping")
            return

        # Look up a human-readable symbol name for this token
        symbol = _TOKEN_SYMBOL_MAP.get(token, "")
        if not token:
            logger.warning(f"🔴 No token in message!")
            return

        # Angel One sends prices in paise → divide by 100
        ltp = message.get('last_traded_price', 0) / 100
        open_price = message.get('open_price_of_the_day', 0) / 100
        high_price = message.get('high_price_of_the_day', 0) / 100
        low_price = message.get('low_price_of_the_day', 0) / 100
        close = message.get('closed_price', 0) / 100
        volume = message.get('volume_trade_for_the_day', 0)
        change = message.get('net_change_value', 0) / 100

        # Calculate percentage change
        chng_pct = ((ltp - close) / close * 100) if close > 0 else 0

        # Timestamp: epoch milliseconds → IST HH:MM:SS
        raw_ts = message.get('exchange_timestamp', 0)
        timestamp = datetime.fromtimestamp(raw_ts / 1000, tz=IST).strftime('%H:%M:%S') if raw_ts else '-'

        latest_ticks[token] = {
            "ltp": ltp,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close,
            "volume": volume,
            "change": change,
            "change_pct": chng_pct,
            "timestamp": timestamp,
            "symbol": symbol,
            "token": token
        }

        # Feed tick to CandleTracker (real-time 5-min OHLC builder)
        candle_tracker.on_tick(token, ltp, volume, open_price, high_price, low_price, close)

        # Log only every 10th tick to avoid spam
        if len(latest_ticks) % 10 == 0:
            logger.info(f"📊 TICK [{token}] LTP={ltp:.2f} | chng%={chng_pct:.2f}% | vol={volume}")

        # Periodic tick-file save (every 30s, so last-known prices survive restarts)
        global _last_tick_save
        if time.time() - _last_tick_save > _TICK_SAVE_INTERVAL:
            _save_ticks()
            _last_tick_save = time.time()

    except Exception as e:
        logger.error(f"on_data error: {e}", exc_info=True)

def on_open(wsapp):
    """
    Called when WebSocket connection opens.
    Subscribe to all tokens from WATCHLIST.
    """
    global _connected
    _connected = True
    logger.info("🔗 WebSocket Connected!")

    try:
        if not WATCHLIST:
            logger.warning("⚠️ WATCHLIST is empty! No subscriptions.")
            return

        logger.info(f"📡 Subscribing to {len(WATCHLIST)} stocks...")

        # Collect ALL tokens as STRINGS — the SDK expects string tokens
        # in the subscription JSON (``"tokens": ["2885"]``, not ``[2885]``).
        # Also, the SDK's ``_on_data`` only processes *binary* frames
        # (data_type == 2). Mode 1 / LTP sends *text* frames that the SDK
        # silently discards, so we must use Mode 2 (Quote) for **all**
        # subscriptions, indices included.
        all_tokens = [str(t) for _, t, _ in WATCHLIST]
        has_index = any(k == "index" for _, _, k in WATCHLIST)
        has_stock = any(k == "stock" for _, _, k in WATCHLIST)

        if has_index:
            logger.info(f"  • Indices: {sum(1 for _, _, k in WATCHLIST if k == 'index')} (Mode 2)")
        if has_stock:
            logger.info(f"  • Stocks: {sum(1 for _, _, k in WATCHLIST if k == 'stock')} (Mode 2)")

        # Batch-subscribe everything in Mode 2 (binary frames → triggers on_data)
        BATCH_SIZE = 950
        for i in range(0, len(all_tokens), BATCH_SIZE):
            batch = all_tokens[i:i + BATCH_SIZE]
            _sws.subscribe(_correlation_id, 2, [
                {"exchangeType": 1, "tokens": batch}
            ])
            batch_num = (i // BATCH_SIZE) + 1
            logger.info(f"✓ Subscribed batch {batch_num}: {len(batch)} tokens in Mode 2")

        logger.info(f"✅ Total subscribed: {len(WATCHLIST)} tokens")

    except Exception as e:
        logger.error(f"🔴 Subscribe error: {e}", exc_info=True)

def on_error(wsapp, error):
    """Called when WebSocket has an error."""
    logger.error(f"🔴 WebSocket Error: {error}")

_last_tick_save = time.time()
_TICK_SAVE_INTERVAL = 30.0  # seconds between tick-file saves

def on_close(wsapp):
    """Called when WebSocket connection closes — triggers auto-reconnect."""
    global _connected
    _connected = False
    _save_ticks()  # preserve last-known prices on disconnect
    logger.info(f"🔌 WebSocket Closed ({len(latest_ticks)} ticks saved) — reconnecting...")
    # Auto-reconnect in background (non-blocking)
    threading.Thread(target=_auto_reconnect, daemon=True).start()

# ==============================================================================
# START / STOP WEBSOCKET
# ==============================================================================

def start_websocket(feed_token=None, watchlist=None):
    """
    Start WebSocket in background thread.

    Args:
        feed_token (str): Angel One feed token (optional - gets from _CREDS if not provided)
        watchlist (list): List of (name, token, kind) tuples (optional)

    Returns:
        dict: {"success": bool, "error": str|None}
    """
    global _sws, _thread, latest_ticks, _connected, WATCHLIST

    # Update watchlist if provided
    if watchlist:
        WATCHLIST = watchlist
        logger.info(f"📋 Updated watchlist with {len(WATCHLIST)} items")

    # Check if already connected
    if _connected and _sws:
        logger.info("ℹ️ WebSocket already running")
        return {"success": True, "message": "Already connected"}

    # Check API connection
    if not is_api_connected():
        return {
            "success": False,
            "error": "Angel One API not connected. Call authenticate() first."
        }

    # Get credentials
    api_key = _CREDS.get("api_key", "")
    client_id = _CREDS.get("client_id", "")
    access_token = get_access_token()

    # Get feed token (from SmartAPI login response)
    if not feed_token:
        # If feed_token not provided, try to get from _CREDS
        feed_token = _CREDS.get("feed_token", "")
        if not feed_token:
            return {
                "success": False,
                "error": "Feed token required. Pass it as argument or set in _CREDS['feed_token']"
            }

    if not api_key or not client_id or not access_token:
        return {
            "success": False,
            "error": "Missing credentials. Ensure API is connected."
        }

    if not WATCHLIST:
        logger.warning("⚠️ WATCHLIST is empty! Use set_watchlist() or pass watchlist param.")

    # Build token→symbol map for the on_data callback
    _build_token_map()

    # Load last-known ticks from disk, then clear for fresh connection.
    _load_saved_ticks()
    latest_ticks = {}

    # Immediately seed from REST API so the table has baseline prices
    # for all watchlist stocks (WS may take time to deliver, or market
    # may be closed). WS ticks will overlay with real-time data as they
    # arrive.
    _seed_ticks_from_rest()
    _connected = True

    logger.info("🚀 Initializing Angel One WebSocket...")
    logger.info(f"   Client: {client_id}")
    logger.info(f"   Watchlist: {len(WATCHLIST)} items")

    try:
        # The WebSocket API expects the full ``Bearer eyJ...`` format
        # in the Authorization header, just like the REST API.
        ws_auth = access_token
        if not ws_auth.startswith("Bearer "):
            ws_auth = f"Bearer {ws_auth}"
        logger.info(f"   WS auth token: {ws_auth[:30]}...")

        _sws = SmartWebSocketV2(
            auth_token=ws_auth,
            api_key=api_key,
            client_code=client_id,
            feed_token=feed_token
        )

        _sws.on_open = on_open
        _sws.on_data = on_data
        _sws.on_error = on_error
        _sws.on_close = on_close

        # WebSocket thread (daemon = auto-kills when main thread exits)
        def _run():
            try:
                logger.info("📡 WebSocket connecting...")
                _sws.connect()
            except Exception as e:
                logger.error(f"🔴 WebSocket connection failed: {e}", exc_info=True)
                global _connected
                _connected = False

        _thread = threading.Thread(target=_run, daemon=True)
        _thread.start()
        logger.info("✓ WebSocket thread started!")

        return {"success": True, "message": "WebSocket connecting..."}

    except Exception as e:
        _connected = False
        logger.error(f"🔴 Failed to start WebSocket: {e}", exc_info=True)
        return {"success": False, "error": str(e)}

# ==============================================================================
# AUTO-RECONNECT — exponential backoff, survives sleep/refresh
# ==============================================================================

def _refresh_angel_auth():
    """Re-authenticate Angel One if the access token is stale or missing.

    Returns True if the API is now authenticated (was already or just-refreshed).
    """
    if not _CREDS.get("access_token"):
        logger.warning("⚠️ Cannot refresh Angel auth: no credentials stored (user must connect in Settings)")
        return False

    # Check token age: Angel One tokens are valid ~24 h.
    # If the token was issued more than 12 h ago, renew it.
    try:
        issued_str = _CREDS.get("token_issued_at", "0")
        issued = float(issued_str) if issued_str else 0
        age = time.time() - issued
        if age < 12 * 3600:
            return True  # still fresh enough
    except (ValueError, TypeError):
        pass

    logger.info("🔄 Angel One token stale — re-authenticating before WS reconnect...")
    try:
        # authenticate() re-uses the stored api_key, client_id, password, totp_secret
        from .angel_margin_calculator import authenticate
        result = authenticate()
        if result.get("ok"):
            logger.info("✅ Angel One re-authenticated successfully")
            return True
        else:
            logger.error(f"🔴 Angel re-auth failed: {result.get('error')}")
            return False
    except Exception as e:
        logger.error(f"🔴 Angel re-auth exception: {e}")
        return False


def _auto_reconnect():
    """Background reconnection loop with exponential backoff.

    Triggered by on_close.  Runs in a daemon thread so it doesn't block
    shutdown.  Stops when _reconnect_stop is set (by stop_websocket).
    Before each attempt, it refreshes the Angel One API credentials so
    we never try to open a WS with a stale jwtToken.
    """
    global _reconnect_active, _connected, _reconnect_stop

    if _reconnect_active:
        logger.info("ℹ️ Reconnect already in progress — skipping")
        return
    _reconnect_active = True
    _reconnect_stop.clear()

    delay = _reconnect_delay
    attempt = 0

    while not _reconnect_stop.is_set():
        if _connected:
            logger.info("✅ WebSocket reconnected successfully")
            break
        attempt += 1
        logger.info(
            f"🔄 Reconnect attempt {attempt}"
            + (f" (max {_reconnect_max_attempts})" if _reconnect_max_attempts else "")
            + f" — waiting {delay:.0f}s..."
        )

        # Wait (check stop-flag every second so we can cancel promptly)
        waited = 0.0
        while waited < delay and not _reconnect_stop.is_set():
            _reconnect_stop.wait(1.0)
            waited += 1.0

        if _reconnect_stop.is_set():
            break

        if _reconnect_max_attempts and attempt >= _reconnect_max_attempts:
            logger.error(f"🔴 Reconnect failed after {attempt} attempts — giving up")
            break

        # ── Refresh API credentials before reconnecting ──────────
        # If the access_token has expired, the WS handshake will be
        # rejected.  Re-auth now so we use a fresh jwtToken + feedToken.
        if not _refresh_angel_auth():
            logger.warning("⚠️ Skipping WS reconnect — cannot refresh API auth")
            delay = min(delay * _reconnect_multiplier, _reconnect_max_delay)
            continue

        # Attempt reconnection
        try:
            result = start_websocket()
            if result.get("success"):
                logger.info(f"✅ Reconnected on attempt {attempt}")
                break
            else:
                logger.warning(f"⚠️ Reconnect attempt {attempt} failed: {result.get('error')}")
        except Exception as e:
            logger.error(f"🔴 Reconnect exception: {e}")

        # Exponential backoff (capped)
        delay = min(delay * _reconnect_multiplier, _reconnect_max_delay)

    _reconnect_active = False


def stop_websocket():
    """Close WebSocket connection and cancel any pending reconnect."""
    global _sws, _connected, _reconnect_stop
    _connected = False
    _reconnect_stop.set()  # cancel any pending reconnect

    if _sws:
        try:
            _sws.close_connection()
            logger.info("🛑 WebSocket stopped.")
            return {"success": True, "message": "Disconnected"}
        except Exception as e:
            logger.error(f"Stop error: {e}")
            return {"success": False, "error": str(e)}

    return {"success": True, "message": "Already disconnected"}

def set_watchlist(watchlist):
    """
    Set the watchlist for WebSocket subscription.

    Args:
        watchlist (list): List of (name, token, kind) tuples
                         e.g., [("RELIANCE", 2885, "stock"), ("NIFTY", 26000, "index")]
    """
    global WATCHLIST
    WATCHLIST = watchlist
    logger.info(f"📋 Watchlist updated: {len(WATCHLIST)} items")
    return {"success": True, "count": len(WATCHLIST)}

def add_to_watchlist(name, token, kind="stock"):
    """Add a single symbol to watchlist and subscribe if WS is connected."""
    global WATCHLIST
    # Check if already exists
    for existing_name, existing_token, _ in WATCHLIST:
        if existing_token == token:
            logger.info(f"ℹ️ {name} already in watchlist")
            return {"success": True, "message": "Already exists", "count": len(WATCHLIST)}

    WATCHLIST.append((name, token, kind))
    logger.info(f"✅ Added {name} (token: {token}) to watchlist")

    # Subscribe via the live WebSocket connection if it's already open
    if _connected and _sws is not None:
        try:
            exchange = 2 if kind == "index" else 1  # 2=NSE indices, 1=NSE equities
            token_str = str(token)
            _sws.subscribe(_correlation_id, 2, [
                {"exchangeType": exchange, "tokens": [token_str]}
            ])
            logger.info(f"📡 Subscribed {name} (token: {token_str}) on live WS")
        except Exception as e:
            logger.error(f"🔴 Subscribe error for {name}: {e}")

    return {"success": True, "count": len(WATCHLIST)}

def remove_from_watchlist(token):
    """Remove a symbol from watchlist by token."""
    global WATCHLIST
    original_count = len(WATCHLIST)
    WATCHLIST = [(n, t, k) for n, t, k in WATCHLIST if t != token]
    removed = original_count - len(WATCHLIST)

    if removed > 0:
        logger.info(f"✅ Removed {removed} item(s) from watchlist")
    else:
        logger.info(f"ℹ️ Token {token} not found in watchlist")

    return {"success": True, "removed": removed, "count": len(WATCHLIST)}

def get_watchlist():
    """Get current watchlist."""
    return {
        "success": True,
        "watchlist": WATCHLIST,
        "count": len(WATCHLIST)
    }

# ==============================================================================
# TOKEN → SYMBOL MAPPING (from scrip master)
# ==============================================================================

def _build_token_map():
    """Build a reverse lookup from token → symbol using the cached scrip master.

    Called once during ``start_websocket`` so that tick data arriving in
    ``on_data`` can be enriched with a human-readable symbol name.
    """
    global _TOKEN_SYMBOL_MAP
    try:
        df = load_master()
        if df is None:
            return
        symbol_col = next((c for c in df.columns if 'symbol' in c.lower()), None)
        token_col  = next((c for c in df.columns if 'token'  in c.lower()), None)
        if not symbol_col or not token_col:
            return
        _TOKEN_SYMBOL_MAP = {
            str(row[token_col]): str(row[symbol_col])
            for _, row in df.iterrows()
        }
        logger.info(f"🗺️ Token→symbol map built: {len(_TOKEN_SYMBOL_MAP)} entries")
    except Exception as e:
        logger.warning(f"⚠️ Could not build token map: {e}")


# ==============================================================================
# GETTERS
# ==============================================================================

def get_latest_ticks():
    """Get all latest tick data."""
    return latest_ticks

def get_tick(token):
    """Get latest tick for a specific token."""
    return latest_ticks.get(str(token))

def get_raw_messages():
    """Get last 5 raw messages for debugging."""
    return _raw_messages

def is_ws_connected():
    """Check if WebSocket is connected."""
    return _connected

def get_subscription_status():
    """Get subscription info for debugging."""
    indices_count = sum(1 for _, _, kind in WATCHLIST if kind == "index")
    stocks_count = sum(1 for _, _, kind in WATCHLIST if kind == "stock")
    ticks_count = len(latest_ticks)

    return {
        "total_subscribed": len(WATCHLIST),
        "indices": indices_count,
        "stocks": stocks_count,
        "ticks_received": ticks_count,
        "connected": _connected,
        "api_connected": is_api_connected()
    }

# ==============================================================================
# TEST FUNCTION
# ==============================================================================

def test_websocket():
    """Test WebSocket connection."""
    print("=" * 60)
    print("ANGEL ONE WEBSOCKET TEST")
    print("=" * 60)

    # Check if API is connected
    if not is_api_connected():
        print("❌ API not connected! Call authenticate() first.")
        print("   Example:")
        print("   from angel_margin_calculator import set_credentials, authenticate")
        print("   set_credentials('API_KEY', 'CLIENT_ID', 'PASSWORD')")
        print("   authenticate()")
        return

    # Set sample watchlist
    set_watchlist([
        ("RELIANCE", 2885, "stock"),
        ("TCS", 2950, "stock"),
        ("INFY", 2945, "stock"),
        ("NIFTY", 26000, "index"),
    ])

    # Start WebSocket
    result = start_websocket(feed_token="YOUR_FEED_TOKEN")
    print(f"📊 Result: {result}")

    if result.get("success"):
        print("\n⏳ Waiting for ticks... (5 seconds)")
        time.sleep(5)

        # Get ticks
        ticks = get_latest_ticks()
        print(f"\n✅ Received {len(ticks)} ticks")

        for token, data in list(ticks.items())[:5]:
            print(f"   {data.get('symbol', token)}: ₹{data.get('ltp', 0):.2f} | chng%: {data.get('change_pct', 0):.2f}%")

        # Get status
        status = get_subscription_status()
        print(f"\n📊 Status: {status}")

        # Stop WebSocket
        stop_websocket()
    else:
        print(f"❌ Failed: {result.get('error')}")

# ── Load last-known ticks at module level so they're available ────
# even before the WebSocket connects (e.g. after server restart
# when the user hasn't re-authenticated yet).
_load_saved_ticks()
# Also attempt REST seed if API is already authenticated — this gives
# baseline LTP data for all 700+ stocks without waiting for a WS tick.
_seed_ticks_from_rest()

if __name__ == "__main__":
    test_websocket()