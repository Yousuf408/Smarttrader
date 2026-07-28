# ==============================================================================
# ANGEL ONE WEBSOCKET - Standalone Version (No Streamlit)
# Integrates with angel_margin_calculator.py for auth
# ==============================================================================

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from logzero import logger
import threading
import time
from datetime import datetime, timezone, timedelta
import json

# Import from your existing modules
from .angel_margin_calculator import (
    _CREDS,
    get_access_token,
    is_connected as is_api_connected,
    ANGEL_PROXIES
)

# ==============================================================================
# CONSTANTS
# ==============================================================================
IST = timezone(timedelta(hours=5, minutes=30))

# ==============================================================================
# GLOBAL STATE
# ==============================================================================
latest_ticks = {}          # {token: {ltp, volume, open, high, low, close, ...}}
_raw_messages = []
_sws = None
_thread = None
_connected = False
_correlation_id = "trading_app_live"

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
# MARKET HOURS CHECK
# ==============================================================================

def is_market_open() -> bool:
    """Check if NSE market is open (9:15 AM - 3:30 PM IST, Mon-Fri)."""
    now = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)

# ==============================================================================
# WEBSOCKET CALLBACKS
# ==============================================================================

def on_data(wsapp, message):
    """Called on every tick from Angel One WebSocket."""
    global latest_ticks, _raw_messages

    try:
        # Store raw message for debugging (last 5 only)
        _raw_messages.append(message)
        if len(_raw_messages) > 5:
            _raw_messages.pop(0)

        token = str(message.get('token', ''))
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
            "symbol": message.get('symbol', ''),
            "token": token
        }

        # Log only every 10th tick to avoid spam
        if len(latest_ticks) % 10 == 0:
            logger.info(f"📊 TICK [{token}] LTP={ltp:.2f} | chng%={chng_pct:.2f}% | vol={volume}")

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

        # Separate indices and stocks
        indices = []  # Mode 1 tokens
        stocks = []   # Mode 2 tokens

        for name, token, kind in WATCHLIST:
            if kind == "index":
                indices.append(token)
            else:
                stocks.append(token)

        logger.info(f"  • Indices: {len(indices)} (Mode 1)")
        logger.info(f"  • Stocks: {len(stocks)} (Mode 2)")

        # Subscribe to indices in Mode 1
        if indices:
            _sws.subscribe(_correlation_id, 1, [
                {"exchangeType": 1, "tokens": indices}
            ])
            logger.info(f"✓ Subscribed {len(indices)} indices in Mode 1")

        # Subscribe to stocks in Mode 2 (batches of 950)
        BATCH_SIZE = 950
        for i in range(0, len(stocks), BATCH_SIZE):
            batch = stocks[i:i + BATCH_SIZE]
            _sws.subscribe(_correlation_id, 2, [
                {"exchangeType": 1, "tokens": batch}
            ])
            batch_num = (i // BATCH_SIZE) + 1
            logger.info(f"✓ Subscribed batch {batch_num}: {len(batch)} stocks in Mode 2")

        logger.info(f"✅ Total subscribed: {len(WATCHLIST)} tokens")

    except Exception as e:
        logger.error(f"🔴 Subscribe error: {e}", exc_info=True)

def on_error(wsapp, error):
    """Called when WebSocket has an error."""
    logger.error(f"🔴 WebSocket Error: {error}")

def on_close(wsapp):
    """Called when WebSocket connection closes."""
    global _connected
    _connected = False
    logger.info("🔌 WebSocket Closed")

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

    # Reset state
    latest_ticks = {}
    _connected = True

    logger.info("🚀 Initializing Angel One WebSocket...")
    logger.info(f"   Client: {client_id}")
    logger.info(f"   Watchlist: {len(WATCHLIST)} items")

    try:
        _sws = SmartWebSocketV2(
            auth_token=access_token,
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

def stop_websocket():
    """Close WebSocket connection."""
    global _sws, _connected
    _connected = False

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
    """Add a single symbol to watchlist."""
    global WATCHLIST
    # Check if already exists
    for existing_name, existing_token, _ in WATCHLIST:
        if existing_token == token:
            logger.info(f"ℹ️ {name} already in watchlist")
            return {"success": True, "message": "Already exists", "count": len(WATCHLIST)}

    WATCHLIST.append((name, token, kind))
    logger.info(f"✅ Added {name} (token: {token}) to watchlist")
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

if __name__ == "__main__":
    test_websocket()