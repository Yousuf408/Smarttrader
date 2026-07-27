---
name: Broker smoke-test safety
description: /api/orders/place* on TradeAlgo Pro fire LIVE Dhan orders; smoke tests must not hit them without explicit user opt-in.
---

The two HTTP order endpoints in `advance_orb/app.py` call `broker.dhan_orders.place_dhan_order(...)` directly. There is no `dryRun` / `paper` flag today — any successful validation curl against `/api/orders/place` or `/api/orders/place-batch` with non-zero quantity will place a real, broker-confirmed order on the user's Dhan account.

**Why:**
- Happened twice in one session: once during initial smoke testing (RELIANCE AMO order placed without warning), and again after fixing the auto-buy bug (RELIANCE INTRADAY batch + RELIANCE AMO via curl validation). User must cancel each from the Dhan app.
- Easy to forget because both endpoints return clean JSON (`{success: true, order_id: ...}`) on success, making it look indistinguishable from a synthetic test response.

**How to apply — when verifying order-placement code:**

1. Default to shape-only validation. Send the request the route REJECTS (`{}` for place, `{"orders":[]}` for place-batch, AMO timings like `"09:30"` that the validator rejects). These return 4xx without ever reaching Dhan.
2. If you must hit the real endpoint to confirm wiring, FIRST:
   - Tell the user you are about to fire a live order — get explicit opt-in.
   - Use the smallest meaningful quantity (usually `1`) and a benign symbol the user has authorised.
   - Provide the resulting order_id in the reply so the user can cancel.
3. The right long-term fix is a `dryRun=true` query param on both endpoints that skips `place_dhan_order` and returns a synthetic `{success:true, dryRun:true, ...}` response. Until that lands, treat any positive smoke test of these endpoints as a real order.

**Not in code yet** — this is a public-protocol hazard, not a bug locally cached in the file.
