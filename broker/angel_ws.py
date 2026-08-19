"""
Angel One SmartAPI WebSocket (SmartStream V2) Integration
Provides real-time streaming market data feeds for NSE/BSE equities and F&O.
"""
import os
import json
import time
import struct
import logging
import threading
from typing import Dict, List, Any, Callable, Optional

logger = logging.getLogger("angel_ws")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s")

# Subscription Modes
MODE_LTP = 1
MODE_QUOTE = 2
MODE_SNAPQUOTE = 3

# Exchange Types
EXCHANGE_NSE_CM = 1
EXCHANGE_NSE_FO = 2
EXCHANGE_BSE_CM = 3
EXCHANGE_BSE_FO = 4
EXCHANGE_MCX_FO = 5
EXCHANGE_NCX_FO = 7
EXCHANGE_CDE_FO = 13

class AngelWebSocket:
    """
    Angel One SmartWebSocket V2 client
    Implements binary stream decoding, heartbeat keep-alive, auto-reconnection,
    and structured market quote broadcasting.
    """
    SMART_STREAM_URL = "wss://smartapisocket.angelone.in/smart-stream"

    def __init__(
        self,
        auth_token: Optional[str] = None,
        api_key: Optional[str] = None,
        client_code: Optional[str] = None,
        feed_token: Optional[str] = None,
        on_data: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_open: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_close: Optional[Callable[[int, str], None]] = None
    ):
        self.auth_token = auth_token or os.environ.get("ANGEL_AUTH_TOKEN") or os.environ.get("ANGEL_JWT_TOKEN", "")
        self.api_key = api_key or os.environ.get("ANGEL_API_KEY", "")
        self.client_code = client_code or os.environ.get("ANGEL_CLIENT_CODE") or os.environ.get("ANGEL_CLIENT_ID", "")
        self.feed_token = feed_token or os.environ.get("ANGEL_FEED_TOKEN", "")
        
        self.on_data_callback = on_data
        self.on_open_callback = on_open
        self.on_error_callback = on_error
        self.on_close_callback = on_close
        
        self.ws = None
        self.is_connected = False
        self.subscriptions: Dict[int, List[str]] = {
            MODE_LTP: [],
            MODE_QUOTE: [],
            MODE_SNAPQUOTE: []
        }
        self._running = False
        self._ping_thread = None
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}

    def get_headers(self) -> Dict[str, str]:
        """Headers required for Angel One SmartStream handshake."""
        return {
            "Authorization": f"Bearer {self.auth_token}" if not self.auth_token.startswith("Bearer ") else self.auth_token,
            "x-api-key": self.api_key,
            "x-client-code": self.client_code,
            "x-feed-token": self.feed_token
        }

    def subscribe(self, mode: int, tokens: List[str], exchange_type: int = EXCHANGE_NSE_CM):
        """
        Subscribe to symbol tokens in LTP, QUOTE, or SNAPQUOTE mode.
        Action: 1 (Subscribe), 0 (Unsubscribe)
        """
        if not tokens:
            return
        
        if mode not in self.subscriptions:
            self.subscriptions[mode] = []
        
        for t in tokens:
            if t not in self.subscriptions[mode]:
                self.subscriptions[mode].append(t)
        
        payload = {
            "action": 1,
            "params": {
                "mode": mode,
                "tokenList": [
                    {
                        "exchangeType": exchange_type,
                        "tokens": tokens
                    }
                ]
            }
        }
        
        logger.info(f"[AngelWS] Subscribing to {len(tokens)} tokens in mode {mode}")
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"[AngelWS] Failed to send subscription packet: {e}")

    def unsubscribe(self, mode: int, tokens: List[str], exchange_type: int = EXCHANGE_NSE_CM):
        """Unsubscribe from symbol tokens."""
        if mode in self.subscriptions:
            self.subscriptions[mode] = [t for t in self.subscriptions[mode] if t not in tokens]
        
        payload = {
            "action": 0,
            "params": {
                "mode": mode,
                "tokenList": [
                    {
                        "exchangeType": exchange_type,
                        "tokens": tokens
                    }
                ]
            }
        }
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"[AngelWS] Failed to send unsubscribe packet: {e}")

    def parse_binary_packet(self, data: bytes) -> Optional[Dict[str, Any]]:
        """
        Parses Little-Endian binary packet from Angel One SmartStream V2.
        Packet format:
          Header (8 bytes):
            - subscription_mode (1 byte, uint8)
            - exchange_type (1 byte, uint8)
            - token (25 bytes char array, ASCII)
            - sequence_number (8 bytes, int64)
            - exchange_timestamp (8 bytes, int64)
          Mode 1 (LTP):
            - ltp (4 bytes, int32) -> in paise / 100.0
          Mode 2 (Quote):
            - ltp (4 bytes, int32)
            - last_traded_qty (8 bytes, int64)
            - avg_traded_price (8 bytes, int64)
            - volume (8 bytes, int64)
            - total_buy_qty (8 bytes, double)
            - total_sell_qty (8 bytes, double)
            - open (4 bytes, int32)
            - high (4 bytes, int32)
            - low (4 bytes, int32)
            - close (4 bytes, int32)
        """
        try:
            if len(data) < 43:
                # Minimum LTP packet size
                return None
            
            sub_mode = struct.unpack('<B', data[0:1])[0]
            exchange_type = struct.unpack('<B', data[1:2])[0]
            token = data[2:27].decode('utf-8', errors='ignore').strip('\x00').strip()
            seq_no = struct.unpack('<q', data[27:35])[0]
            exchange_ts = struct.unpack('<q', data[35:43])[0]
            
            parsed: Dict[str, Any] = {
                "mode": sub_mode,
                "exchange_type": exchange_type,
                "token": token,
                "sequence_number": seq_no,
                "timestamp": exchange_ts,
                "received_at": time.time()
            }
            
            if sub_mode == MODE_LTP:
                if len(data) >= 47:
                    ltp_paise = struct.unpack('<i', data[43:47])[0]
                    parsed["ltp"] = round(ltp_paise / 100.0, 2)
            elif sub_mode in (MODE_QUOTE, MODE_SNAPQUOTE):
                if len(data) >= 95:
                    ltp_paise = struct.unpack('<i', data[43:47])[0]
                    ltq = struct.unpack('<q', data[47:55])[0]
                    atp_paise = struct.unpack('<q', data[55:63])[0]
                    vol = struct.unpack('<q', data[63:71])[0]
                    tbq = struct.unpack('<d', data[71:79])[0]
                    tsq = struct.unpack('<d', data[79:87])[0]
                    open_paise = struct.unpack('<i', data[87:91])[0]
                    high_paise = struct.unpack('<i', data[91:95])[0]
                    low_paise = struct.unpack('<i', data[95:99])[0] if len(data) >= 99 else 0
                    close_paise = struct.unpack('<i', data[99:103])[0] if len(data) >= 103 else 0
                    
                    parsed.update({
                        "ltp": round(ltp_paise / 100.0, 2),
                        "last_traded_qty": ltq,
                        "avg_traded_price": round(atp_paise / 100.0, 2),
                        "volume": vol,
                        "total_buy_qty": tbq,
                        "total_sell_qty": tsq,
                        "open": round(open_paise / 100.0, 2),
                        "high": round(high_paise / 100.0, 2),
                        "low": round(low_paise / 100.0, 2),
                        "close": round(close_paise / 100.0, 2)
                    })
            
            self._latest_ticks[token] = parsed
            return parsed
        except Exception as e:
            logger.debug(f"[AngelWS] Binary parsing exception: {e}")
            return None

    def _on_ws_open(self, ws):
        self.is_connected = True
        logger.info("[AngelWS] Connected to Angel One SmartStream V2.")
        
        # Resubscribe to saved tokens
        for mode, tokens in self.subscriptions.items():
            if tokens:
                self.subscribe(mode, tokens)
        
        if self.on_open_callback:
            self.on_open_callback()

    def _on_ws_message(self, ws, message):
        try:
            if isinstance(message, bytes):
                tick = self.parse_binary_packet(message)
                if tick and self.on_data_callback:
                    self.on_data_callback(tick)
            elif isinstance(message, str):
                # Text or JSON response (e.g. heartbeat response or subscription ack)
                if message.strip() == "pong":
                    return
                try:
                    payload = json.loads(message)
                    if self.on_data_callback:
                        self.on_data_callback(payload)
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.error(f"[AngelWS] Error handling message: {e}")

    def _on_ws_error(self, ws, error):
        logger.error(f"[AngelWS] WebSocket error: {error}")
        if self.on_error_callback:
            self.on_error_callback(error)

    def _on_ws_close(self, ws, close_status_code, close_msg):
        self.is_connected = False
        logger.warning(f"[AngelWS] WebSocket disconnected ({close_status_code}): {close_msg}")
        if self.on_close_callback:
            self.on_close_callback(close_status_code, close_msg)

    def _ping_loop(self):
        """Heartbeat ping every 30 seconds to maintain connection alive."""
        while self._running:
            time.sleep(30)
            if self.ws and self.is_connected:
                try:
                    self.ws.send("ping")
                except Exception as e:
                    logger.debug(f"[AngelWS] Ping error: {e}")

    def connect(self):
        """Starts WebSocket connection loop in a background daemon thread."""
        self._running = True
        logger.info("Connecting to Angel One SmartAPI WebSocket...")
        
        try:
            import websocket
        except ImportError:
            logger.warning("[AngelWS] 'websocket-client' library not installed. Simulation mode active.")
            return

        def _run():
            while self._running:
                try:
                    headers = self.get_headers()
                    self.ws = websocket.WebSocketApp(
                        self.SMART_STREAM_URL,
                        header=headers,
                        on_open=self._on_ws_open,
                        on_message=self._on_ws_message,
                        on_error=self._on_ws_error,
                        on_close=self._on_ws_close
                    )
                    
                    if not self._ping_thread or not self._ping_thread.is_alive():
                        self._ping_thread = threading.Thread(target=self._ping_loop, daemon=True)
                        self._ping_thread.start()
                    
                    self.ws.run_forever(ping_interval=30, ping_timeout=10)
                except Exception as e:
                    logger.error(f"[AngelWS] Connection loop failed: {e}. Retrying in 5s...")
                    time.sleep(5)

        ws_thread = threading.Thread(target=_run, daemon=True)
        ws_thread.start()

    def close(self):
        """Disconnect and stop background threads."""
        self._running = False
        self.is_connected = False
        if self.ws:
            self.ws.close()

    def get_latest_tick(self, token: str) -> Optional[Dict[str, Any]]:
        """Retrieve most recent tick for a symbol token."""
        return self._latest_ticks.get(str(token))

