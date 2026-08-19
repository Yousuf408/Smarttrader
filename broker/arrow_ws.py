"""
Arrow Trade (arrow.trade / iRage Broking) WebSocket Streaming Client
Provides ultra-low latency real-time tick-by-tick market data (LTP, Quote, Depth)
and instant order execution updates.
"""
import os
import json
import time
import hmac
import hashlib
import logging
import threading
from typing import Dict, List, Any, Callable, Optional, Union

logger = logging.getLogger("arrow_ws")
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] [%(name)s] [%(levelname)s] %(message)s")

# Subscription Modes
MODE_LTP = "ltp"
MODE_QUOTE = "quote"
MODE_DEPTH = "depth"
MODE_ORDERS = "orders"

# Exchange Types
EXCHANGE_NSE = "NSE"
EXCHANGE_BSE = "BSE"
EXCHANGE_NFO = "NFO"
EXCHANGE_MCX = "MCX"

class ArrowWebSocket:
    """
    Arrow Trade WebSocket client for streaming live market quotes,
    LTP updates, depth, and order updates.
    """
    DEFAULT_WS_URL = "wss://api.arrow.trade/ws/v1/stream"

    def __init__(
        self,
        app_id: Optional[str] = None,
        app_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        ws_url: Optional[str] = None,
        on_data: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_order_update: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_open: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[Exception], None]] = None,
        on_close: Optional[Callable[[int, str], None]] = None
    ):
        # 1. Load configuration from parameters, environment variables, or config file
        self.app_id = app_id or os.environ.get("ARROW_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("ARROW_APP_SECRET", "")
        self.access_token = access_token or os.environ.get("ARROW_ACCESS_TOKEN", "")
        self.ws_url = ws_url or os.environ.get("ARROW_WS_URL", self.DEFAULT_WS_URL)
        
        self._load_config_file_if_empty()

        # Callbacks
        self.on_data_callback = on_data
        self.on_order_update_callback = on_order_update
        self.on_open_callback = on_open
        self.on_error_callback = on_error
        self.on_close_callback = on_close

        # Internal state
        self.ws = None
        self.is_connected = False
        self._running = False
        self._ping_thread = None
        self._latest_ticks: Dict[str, Dict[str, Any]] = {}
        
        # Subscribed symbols grouped by mode
        self.subscriptions: Dict[str, List[str]] = {
            MODE_LTP: [],
            MODE_QUOTE: [],
            MODE_DEPTH: []
        }

    def _load_config_file_if_empty(self):
        """Attempts to load Arrow credentials from config/arrow_config.json if not present."""
        if not self.app_id or not self.app_secret:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(base_dir, "config", "arrow_config.json")
            if os.path.exists(config_path):
                try:
                    with open(config_path, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                        if not self.app_id:
                            self.app_id = cfg.get("app_id", "")
                        if not self.app_secret:
                            self.app_secret = cfg.get("app_secret", "")
                        if not self.ws_url or self.ws_url == self.DEFAULT_WS_URL:
                            self.ws_url = cfg.get("ws_url", self.DEFAULT_WS_URL)
                except Exception as e:
                    logger.debug(f"[ArrowWS] Error reading arrow_config.json: {e}")

    def generate_auth_signature(self, timestamp: int) -> str:
        """Generates HMAC-SHA256 signature for Arrow authentication."""
        message = f"{self.app_id}:{timestamp}".encode('utf-8')
        secret = self.app_secret.encode('utf-8')
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    def get_auth_headers(self) -> Dict[str, str]:
        """Headers required for Arrow Trade WebSocket handshake."""
        timestamp = int(time.time())
        signature = self.generate_auth_signature(timestamp) if self.app_secret else ""
        headers = {
            "X-App-ID": self.app_id,
            "X-Timestamp": str(timestamp),
            "X-Signature": signature
        }
        if self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}" if not self.access_token.startswith("Bearer ") else self.access_token
        return headers

    def authenticate_on_open(self):
        """Sends auth payload after WebSocket connection is established."""
        timestamp = int(time.time())
        signature = self.generate_auth_signature(timestamp) if self.app_secret else ""
        auth_msg = {
            "action": "auth",
            "params": {
                "app_id": self.app_id,
                "timestamp": timestamp,
                "signature": signature,
                "token": self.access_token
            }
        }
        try:
            if self.ws and self.is_connected:
                self.ws.send(json.dumps(auth_msg))
                logger.info(f"[ArrowWS] Sent authentication handshake for App ID {self.app_id[:4]}***")
        except Exception as e:
            logger.error(f"[ArrowWS] Failed to send authentication handshake: {e}")

    def subscribe(self, symbols: List[str], mode: str = MODE_QUOTE, exchange: str = EXCHANGE_NSE):
        """
        Subscribe to live streaming market data for symbols.
        Modes: 'ltp', 'quote', 'depth', 'orders'
        """
        if not symbols:
            return
        
        mode = mode.lower()
        if mode not in self.subscriptions:
            self.subscriptions[mode] = []
        
        cleaned_symbols = []
        for s in symbols:
            clean = s.upper().strip()
            if clean not in self.subscriptions[mode]:
                self.subscriptions[mode].append(clean)
            cleaned_symbols.append(clean)
        
        payload = {
            "action": "subscribe",
            "params": {
                "mode": mode,
                "exchange": exchange.upper(),
                "symbols": cleaned_symbols
            }
        }

        logger.info(f"[ArrowWS] Subscribing to {len(cleaned_symbols)} symbols in mode '{mode}' on {exchange}")
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"[ArrowWS] Failed to send subscription message: {e}")

    def unsubscribe(self, symbols: List[str], mode: str = MODE_QUOTE, exchange: str = EXCHANGE_NSE):
        """Unsubscribe from symbol updates."""
        mode = mode.lower()
        if mode in self.subscriptions:
            self.subscriptions[mode] = [s for s in self.subscriptions[mode] if s.upper() not in [x.upper() for x in symbols]]
        
        payload = {
            "action": "unsubscribe",
            "params": {
                "mode": mode,
                "exchange": exchange.upper(),
                "symbols": [s.upper().strip() for s in symbols]
            }
        }
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(payload))
            except Exception as e:
                logger.error(f"[ArrowWS] Failed to send unsubscribe message: {e}")

    def subscribe_order_updates(self):
        """Subscribe to real-time order execution notifications."""
        payload = {
            "action": "subscribe_orders",
            "params": {
                "app_id": self.app_id
            }
        }
        if self.ws and self.is_connected:
            try:
                self.ws.send(json.dumps(payload))
                logger.info("[ArrowWS] Subscribed to live order updates")
            except Exception as e:
                logger.error(f"[ArrowWS] Failed to subscribe to order updates: {e}")

    def parse_incoming_message(self, message: Union[str, bytes]):
        """Parses JSON or structured message payload from Arrow WebSocket."""
        try:
            if isinstance(message, bytes):
                message = message.decode('utf-8', errors='ignore')
            
            data = json.loads(message)
            msg_type = data.get("type", "").lower()
            
            # 1. Heartbeat / pong response
            if msg_type == "pong" or data.get("action") == "pong":
                return
            
            # 2. Authentication confirmation
            if msg_type == "auth_ack" or data.get("status") == "authenticated":
                logger.info("[ArrowWS] Authentication confirmed by Arrow server")
                # Resubscribe to symbols
                for sub_mode, symbols in self.subscriptions.items():
                    if symbols:
                        self.subscribe(symbols, mode=sub_mode)
                return

            # 3. Market Data Tick (LTP, Quote, Depth)
            if msg_type in ("tick", "quote", "ltp", "depth") or "ltp" in data or "price" in data:
                symbol = str(data.get("symbol") or data.get("tradingsymbol") or data.get("token", "")).upper()
                ltp = float(data.get("ltp") or data.get("price") or data.get("last_price") or 0.0)
                chg_pct = float(data.get("change_pct") or data.get("chg") or 0.0)
                vol = int(data.get("volume") or data.get("vol") or 0)
                
                parsed_tick = {
                    "broker": "arrow",
                    "symbol": symbol,
                    "ltp": ltp,
                    "change_pct": chg_pct,
                    "volume": vol,
                    "open": float(data.get("open") or 0.0),
                    "high": float(data.get("high") or 0.0),
                    "low": float(data.get("low") or 0.0),
                    "close": float(data.get("close") or 0.0),
                    "vwap": float(data.get("vwap") or 0.0),
                    "depth": data.get("depth", {}),
                    "timestamp": data.get("timestamp") or time.time()
                }

                if symbol:
                    self._latest_ticks[symbol] = parsed_tick

                if self.on_data_callback:
                    self.on_data_callback(parsed_tick)

            # 4. Order Execution Update
            elif msg_type in ("order_update", "order_status", "execution"):
                logger.info(f"[ArrowWS] Order update received: {data}")
                if self.on_order_update_callback:
                    self.on_order_update_callback(data)
                elif self.on_data_callback:
                    self.on_data_callback(data)

        except json.JSONDecodeError:
            logger.debug(f"[ArrowWS] Received non-JSON message: {message}")
        except Exception as e:
            logger.error(f"[ArrowWS] Error handling message: {e}")

    def _on_ws_open(self, ws):
        self.is_connected = True
        logger.info(f"[ArrowWS] Connected to Arrow Trade WebSocket ({self.ws_url}).")
        
        # Authenticate
        self.authenticate_on_open()
        
        if self.on_open_callback:
            self.on_open_callback()

    def _on_ws_message(self, ws, message):
        self.parse_incoming_message(message)

    def _on_ws_error(self, ws, error):
        logger.error(f"[ArrowWS] WebSocket error: {error}")
        if self.on_error_callback:
            self.on_error_callback(error)

    def _on_ws_close(self, ws, close_status_code, close_msg):
        self.is_connected = False
        logger.warning(f"[ArrowWS] WebSocket closed (code: {close_status_code}): {close_msg}")
        if self.on_close_callback:
            self.on_close_callback(close_status_code, close_msg)

    def _ping_loop(self):
        """Sends keep-alive ping every 30 seconds."""
        while self._running:
            time.sleep(30)
            if self.ws and self.is_connected:
                try:
                    self.ws.send(json.dumps({"action": "ping", "timestamp": int(time.time())}))
                except Exception as e:
                    logger.debug(f"[ArrowWS] Ping error: {e}")

    def connect(self):
        """Starts WebSocket connection in a daemon thread."""
        self._running = True
        logger.info(f"Connecting to Arrow Trade WebSocket for App ID {self.app_id}...")

        try:
            import websocket
        except ImportError:
            logger.warning("[ArrowWS] 'websocket-client' package not found. Running in simulation mode.")
            return

        def _run():
            while self._running:
                try:
                    headers = self.get_auth_headers()
                    self.ws = websocket.WebSocketApp(
                        self.ws_url,
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
                    logger.error(f"[ArrowWS] Connection loop exception: {e}. Retrying in 5s...")
                    time.sleep(5)

        ws_thread = threading.Thread(target=_run, daemon=True)
        ws_thread.start()

    def close(self):
        """Closes WebSocket and stops background workers."""
        self._running = False
        self.is_connected = False
        if self.ws:
            self.ws.close()

    def get_latest_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Retrieves the latest cached tick for a symbol."""
        return self._latest_ticks.get(symbol.upper().strip())


if __name__ == '__main__':
    # Test runner example
    def handle_tick(tick):
        print(f"[TEST TICK] {tick['symbol']}: ₹{tick['ltp']} ({tick['change_pct']}%) Vol:{tick['volume']}")

    client = ArrowWebSocket(
        app_id="70de391959b7",
        app_secret="d7ede1e3cab41b6807ea9f145db71227067236a6940ec55db85f36313d501c0c",
        on_data=handle_tick
    )
    print("Arrow WebSocket client initialized.")
