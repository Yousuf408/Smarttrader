---
name: Angel One WS auto-reconnect
description: WebSocket auto-reconnect with credential refresh and Angel One auto-renew loop
---

## Problem
- Angel One WebSocket had no reconnect: on_close just logged and quit.
- Broker API token had no auto-renew (unlike Dhan which uses /RenewToken).
- Frontend SSE watchdog fired repeatedly because heartbeats were SSE comments (`:`) which don't trigger EventSource onmessage.

## Fixes

### 1. Backend WS reconnect (broker/angel_ws.py)
- `_auto_reconnect()` — daemon thread with exponential backoff (5s -> 10s -> 20s ... 120s max).
- `_refresh_angel_auth()` — checks token age (>12h -> re-auth via stored creds) before each WS reconnect attempt.
- `stop_websocket()` sets `_reconnect_stop` to cancel any pending reconnect.

### 2. Angel One auto-renew loop (advance_orb/app.py)
- `_angel_auto_renew_loop()` — background asyncio task checks token age every 5 min.
- Re-authenticates via stored TOTP when age > 12h.
- Runs alongside Dhan's existing loop in the lifespan.

### 3. Frontend SSE watchdog (js/screener.js, js/bigplayers.js)
- 10s message-silence watchdog force-reconnects the EventSource.
- Backend heartbeat changed from SSE comment (`: heartbeat`) to proper `data:` line so onmessage fires and resets the watchdog.

### 4. Token tracking (broker/angel_margin_calculator.py)
- `_CREDS["token_issued_at"]` — epoch seconds set during authenticate().

**Why:** Page refresh or screen-sleep kills the Angel One WS; without auto-reconnect the app would stay offline until user manually reconnected in Settings.

**How to apply:** Any new WebSocket integration in this project should follow the same pattern — on_close triggers reconnect, credential refresh happens before each attempt, and frontend SSE heartbeats must be real data lines (not comments) so the watchdog can monitor them.
