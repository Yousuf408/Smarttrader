/**
 * Arrow Trade Real-Time WebSocket Streaming Service (Node.js)
 * Connects directly to wss://api.arrow.trade/ws/v1/stream
 * Feeds tick-by-tick market data into the Screener, Portfolio, and Dashboard.
 */
import WebSocket from 'ws';
import crypto from 'crypto';

class ArrowStreamService {
  constructor() {
    this.appId = process.env.ARROW_APP_ID || '70de391959b7';
    this.appSecret = process.env.ARROW_APP_SECRET || 'd7ede1e3cab41b6807ea9f145db71227067236a6940ec55db85f36313d501c0c';
    this.wsUrl = process.env.ARROW_WS_URL || 'wss://api.arrow.trade/ws/v1/stream';
    this.ws = null;
    this.isConnected = false;
    this.subscriptions = new Set();
    this.latestTicks = new Map();
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
    this.onTickCallbacks = new Set();
  }

  generateSignature(timestamp) {
    return crypto
      .createHmac('sha256', this.appSecret)
      .update(String(timestamp))
      .digest('hex');
  }

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const timestamp = Date.now();
    const signature = this.generateSignature(timestamp);
    const headers = {
      'x-app-id': this.appId,
      'x-timestamp': String(timestamp),
      'x-signature': signature
    };

    try {
      this.ws = new WebSocket(this.wsUrl, { 
        headers,
        handshakeTimeout: 5000 
      });

      this.ws.on('open', () => {
        this.isConnected = true;
        console.log('[ArrowWS] ✅ Connected to Arrow Trade WebSocket successfully.');
        
        // Send explicit auth handshake
        this.send({
          action: 'auth',
          app_id: this.appId,
          timestamp: timestamp,
          signature: signature
        });

        // Resubscribe active watchlist
        this.resubscribe();

        // Start heartbeat ping loop
        this.startHeartbeat();
      });

      this.ws.on('message', (raw) => {
        try {
          const msg = JSON.parse(raw.toString());
          this.handleMessage(msg);
        } catch (err) {
          this.handleBinaryMessage(raw);
        }
      });

      this.ws.on('close', (code, reason) => {
        this.isConnected = false;
        this.stopHeartbeat();
        this.scheduleReconnect(15000);
      });

      this.ws.on('error', (err) => {
        // Suppress noisy repeat stack traces on 404 or connection rejection
        console.warn(`[ArrowWS] WebSocket connection notice (${this.wsUrl}): ${err.message}`);
      });
    } catch (e) {
      console.warn(`[ArrowWS] Connection init notice: ${e.message}`);
      this.scheduleReconnect(15000);
    }
  }

  startHeartbeat() {
    this.stopHeartbeat();
    this.heartbeatTimer = setInterval(() => {
      if (this.isConnected && this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.send({ action: 'ping', timestamp: Date.now() });
      }
    }, 25000);
  }

  stopHeartbeat() {
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
  }

  scheduleReconnect(delayMs = 15000) {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
    }, delayMs);
  }



  send(data) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(data));
      } catch (err) {
        console.error('[ArrowWS] Send error:', err.message);
      }
    }
  }

  subscribeSymbols(symbols) {
    if (!symbols || symbols.length === 0) return;
    symbols.forEach(s => this.subscriptions.add(s.toUpperCase()));
    if (this.isConnected) {
      this.send({
        action: 'subscribe',
        mode: 'quote',
        symbols: Array.from(this.subscriptions).map(s => s.startsWith('NSE:') ? s : `NSE:${s}`)
      });
    }
  }

  resubscribe() {
    if (this.subscriptions.size > 0) {
      this.send({
        action: 'subscribe',
        mode: 'quote',
        symbols: Array.from(this.subscriptions).map(s => s.startsWith('NSE:') ? s : `NSE:${s}`)
      });
    }
  }

  handleMessage(msg) {
    if (!msg) return;

    if (msg.action === 'pong' || msg.type === 'pong') {
      return;
    }

    // Handle tick packets
    if (msg.type === 'tick' || msg.type === 'quote' || msg.ltp != null || msg.price != null) {
      let rawSym = msg.symbol || msg.tradingsymbol || msg.name || '';
      let cleanSym = rawSym.replace(/^(NSE|BSE):/i, '').replace(/-EQ$/i, '').toUpperCase();
      if (!cleanSym) return;

      const tick = {
        symbol: cleanSym,
        ltp: Number(msg.ltp ?? msg.price ?? msg.last_price ?? 0),
        change_pct: Number(msg.change_pct ?? msg.change_percent ?? msg.chg_pct ?? 0),
        change: Number(msg.change ?? msg.chg ?? 0),
        open: Number(msg.open ?? msg.open915 ?? 0),
        high: Number(msg.high ?? 0),
        low: Number(msg.low ?? 0),
        close: Number(msg.close ?? msg.prev_close ?? 0),
        volume: Number(msg.volume ?? msg.vol ?? 0),
        timestamp: new Date().toLocaleTimeString('en-IN', { hour12: false })
      };

      if (tick.ltp > 0) {
        this.latestTicks.set(cleanSym, tick);
        this.latestTicks.set(`${cleanSym}-EQ`, tick);

        // Notify subscribers
        for (const cb of this.onTickCallbacks) {
          try { cb(cleanSym, tick); } catch (_) {}
        }
      }
    } else if (Array.isArray(msg.ticks) || Array.isArray(msg.data)) {
      const list = msg.ticks || msg.data;
      list.forEach(t => this.handleMessage(t));
    }
  }

  handleBinaryMessage(buf) {
    // If binary packed struct format is returned
    // Extensible parser for binary exchange feed packets
  }

  onTick(callback) {
    this.onTickCallbacks.add(callback);
    return () => this.onTickCallbacks.delete(callback);
  }

  getTick(symbol) {
    const clean = String(symbol || '').replace(/^(NSE|BSE):/i, '').replace(/-EQ$/i, '').toUpperCase();
    return this.latestTicks.get(clean) || null;
  }

  getAllTicks() {
    const out = {};
    for (const [k, v] of this.latestTicks.entries()) {
      out[k] = v;
    }
    return out;
  }

  updateManualTick(symbol, tickData) {
    const clean = String(symbol || '').replace(/^(NSE|BSE):/i, '').replace(/-EQ$/i, '').toUpperCase();
    this.latestTicks.set(clean, tickData);
    this.latestTicks.set(`${clean}-EQ`, tickData);
  }
}

export const arrowStreamService = new ArrowStreamService();
