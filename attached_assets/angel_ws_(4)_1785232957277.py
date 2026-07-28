# angel_ws.py - MODIFIED for config.py
# Place this file in your ROOT folder (same level as app.py)

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from logzero import logger
import threading
import time
from datetime import datetime, timezone, timedelta
from config import STOCKS_WATCHLIST  # ← IMPORT from config.py

IST = timezone(timedelta(hours=5, minutes=30))

# ── Global state — shared across all threads ──────────────────
latest_ticks    = {}          # {token: {ltp, volume, open, high, low, close, ...}}
_raw_messages   = []
_sws            = None
_thread         = None
_connected      = False
_correlation_id = "stockscan_live"
# ───────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────
# SECTION 1 — MARKET HOURS CHECK
# ─────────────────────────────────────────────────────────────

def _is_market_open() -> bool:
    """Check if NSE market is open (9:15 AM - 3:30 PM IST, Mon-Fri)."""
    now  = datetime.now(IST)
    if now.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    mins = now.hour * 60 + now.minute
    return (9 * 60 + 15) <= mins <= (15 * 60 + 30)


# ─────────────────────────────────────────────────────────────
# SECTION 2 — WEBSOCKET CALLBACKS
# ─────────────────────────────────────────────────────────────

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
        ltp        = message.get('last_traded_price', 0) / 100
        open_price = message.get('open_price_of_the_day', 0) / 100
        high_price = message.get('high_price_of_the_day', 0) / 100
        low_price  = message.get('low_price_of_the_day', 0) / 100
        close      = message.get('closed_price', 0) / 100
        volume     = message.get('volume_trade_for_the_day', 0)
        change     = message.get('net_change_value', 0) / 100
        chng_pct   = ((ltp - close) / close * 100) if close > 0 else 0

        # Timestamp: epoch milliseconds → IST HH:MM:SS
        raw_ts    = message.get('exchange_timestamp', 0)
        timestamp = datetime.fromtimestamp(raw_ts / 1000, tz=IST).strftime('%H:%M:%S') if raw_ts else '-'

        latest_ticks[token] = {
            "ltp"        : ltp,
            "open"       : open_price,
            "high"       : high_price,
            "low"        : low_price,
            "close"      : close,
            "volume"     : volume,
            "change"     : change,
            "change_pct" : chng_pct,
            "timestamp"  : timestamp,
        }

        logger.info(f"✓ TICK [{token}] LTP={ltp:.2f} | chng%={chng_pct:.2f}% | vol={volume} | time={timestamp}")

    except Exception as e:
        logger.error(f"on_data error: {e}", exc_info=True)


def on_open(wsapp):
    """
    Called when WebSocket connection opens.
    Subscribe to ALL tokens from config.py STOCKS_WATCHLIST.
    """
    global _connected
    _connected = True
    logger.info("🔗 WebSocket Connected!")

    try:
        logger.info(f"📡 Subscribing to {len(STOCKS_WATCHLIST)} stocks from config.py...")

        # Extract tokens from config
        # Format: [(name, token, kind), ...]
        indices = []  # Mode 1 tokens
        stocks = []   # Mode 2 tokens

        for name, token, kind in STOCKS_WATCHLIST:
            if kind == "index":
                indices.append(token)
            else:
                stocks.append(token)

        logger.info(f"  • Indices: {len(indices)} (Mode 1) - {indices}")
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

        logger.info(f"✅ Total subscribed: {len(STOCKS_WATCHLIST)} tokens ({len(indices)} indices + {len(stocks)} stocks)")

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


# ─────────────────────────────────────────────────────────────
# SECTION 3 — START / STOP
# ─────────────────────────────────────────────────────────────

def start_websocket(jwt_token, api_key, client_id, feed_token):
    """Start WebSocket in background thread."""
    global _sws, _thread, latest_ticks, _connected

    # Reset on new connection
    latest_ticks = {}
    _connected   = True

    logger.info("🚀 Initializing Angel One WebSocket...")

    _sws = SmartWebSocketV2(
        auth_token  = jwt_token,
        api_key     = api_key,
        client_code = client_id,
        feed_token  = feed_token
    )

    _sws.on_open  = on_open
    _sws.on_data  = on_data
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


def stop_websocket():
    """Close WebSocket connection."""
    global _sws, _connected
    _connected = False
    
    if _sws:
        try:
            _sws.close_connection()
            logger.info("🛑 WebSocket stopped.")
        except Exception as e:
            logger.error(f"Stop error: {e}")


# ─────────────────────────────────────────────────────────────
# SECTION 4 — GETTERS
# ─────────────────────────────────────────────────────────────

def get_latest_ticks():
    """Get all latest tick data."""
    return latest_ticks


def get_raw_messages():
    """Get last 5 raw messages for debugging."""
    return _raw_messages


def is_connected():
    """Check if WebSocket is connected."""
    return _connected


def get_subscription_status():
    """Get subscription info for debugging."""
    indices_count = sum(1 for _, _, kind in STOCKS_WATCHLIST if kind == "index")
    stocks_count = sum(1 for _, _, kind in STOCKS_WATCHLIST if kind == "stock")
    ticks_count = len(latest_ticks)
    
    return {
        "total_subscribed": len(STOCKS_WATCHLIST),
        "indices": indices_count,
        "stocks": stocks_count,
        "ticks_received": ticks_count,
        "connected": _connected
    }
