# ==============================================================================
# test_websocket.py - Test Angel One WebSocket
# ==============================================================================

import time
from angel_margin_calculator import (
    set_credentials, 
    authenticate, 
    get_feed_token,
    is_connected as is_api_connected
)
from angel_ws import (
    set_watchlist,
    start_websocket,
    stop_websocket,
    get_latest_ticks,
    get_subscription_status,
    is_ws_connected
)

# ==============================================================================
# YOUR CREDENTIALS (Replace with actual values)
# ==============================================================================
API_KEY = "QFectj5C"
CLIENT_ID = "IIRA29771"
PASSWORD = "YOUR_PASSWORD"
TOTP_SECRET = "JFTG3DYADWLYSW6FC6RVV4THWM"  # Optional

# ==============================================================================
# WATCHLIST (Symbols to track)
# ==============================================================================
WATCHLIST = [
    # Format: (name, token, kind)
    # kind: "index" or "stock"
    ("NIFTY", 26000, "index"),
    ("BANKNIFTY", 26009, "index"),
    ("RELIANCE", 2885, "stock"),
    ("TCS", 2950, "stock"),
    ("INFY", 2945, "stock"),
    ("HDFC", 2970, "stock"),
    ("ICICIBANK", 2982, "stock"),
    ("SBIN", 3001, "stock"),
]

# ==============================================================================
# TEST FUNCTION
# ==============================================================================

def test_websocket():
    """Test WebSocket connection."""
    print("=" * 70)
    print("🧪 TESTING ANGEL ONE WEBSOCKET")
    print("=" * 70)

    # Step 1: Set credentials
    print("\n📝 Step 1: Setting credentials...")
    set_credentials(API_KEY, CLIENT_ID, PASSWORD, TOTP_SECRET)

    # Step 2: Authenticate
    print("\n🔐 Step 2: Authenticating...")
    auth_result = authenticate()

    if not auth_result.get("ok"):
        print(f"❌ Authentication failed: {auth_result.get('error')}")
        return

    print("✅ Authentication successful!")
    print(f"   Access Token: {auth_result.get('access_token', '')[:30]}...")

    # Step 3: Check feed token
    print("\n📡 Step 3: Checking feed token...")
    feed_token = get_feed_token()
    if feed_token:
        print(f"✅ Feed token available: {feed_token[:20]}...")
    else:
        print("⚠️ No feed token found! WebSocket won't work without it.")
        print("   Feed token should be provided in authentication response.")
        return

    # Step 4: Set watchlist
    print(f"\n📋 Step 4: Setting watchlist ({len(WATCHLIST)} items)...")
    set_watchlist(WATCHLIST)

    # Step 5: Start WebSocket
    print("\n🔗 Step 5: Starting WebSocket...")
    result = start_websocket(feed_token=feed_token)

    if not result.get("success"):
        print(f"❌ WebSocket failed: {result.get('error')}")
        return

    print("✅ WebSocket started!")

    # Step 6: Wait for ticks
    print("\n⏳ Step 6: Waiting for ticks (10 seconds)...")
    time.sleep(10)

    # Step 7: Check status
    print("\n📊 Step 7: Checking status...")
    status = get_subscription_status()
    print(f"   Connected: {status.get('connected')}")
    print(f"   Subscribed: {status.get('total_subscribed')}")
    print(f"   Ticks received: {status.get('ticks_received')}")

    # Step 8: Get ticks
    print("\n💰 Step 8: Latest ticks:")
    ticks = get_latest_ticks()

    if ticks:
        print(f"   ✅ Received {len(ticks)} ticks\n")
        for token, data in list(ticks.items())[:10]:  # Show first 10
            symbol = data.get('symbol', token)
            ltp = data.get('ltp', 0)
            change_pct = data.get('change_pct', 0)
            timestamp = data.get('timestamp', '-')
            print(f"   {symbol:15} ₹{ltp:8.2f} | change: {change_pct:6.2f}% | time: {timestamp}")

        if len(ticks) > 10:
            print(f"   ... and {len(ticks) - 10} more")
    else:
        print("   ⚠️ No ticks received yet")

    # Step 9: Stop WebSocket
    print("\n🛑 Step 9: Stopping WebSocket...")
    stop_websocket()

    print("\n✅ Test completed!")
    return True

# ==============================================================================
# RUN TEST
# ==============================================================================

if __name__ == "__main__":
    test_websocket()