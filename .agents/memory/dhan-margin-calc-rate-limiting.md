---
name: Dhan margin calculator 429 limits
description: Bulk parallel calls to Dhan's /margincalculator endpoint throttle to HTTP 429; share an instrument CSV cache before Auto-Buy / bulk-margin features ship.
---

Dhan's margin calculator returns HTTP **429** when called in tightly-looped bulk batches (we observed throttling when looping ~130 symbols back-to-back inside a single FastAPI request). The screener currently silently sets `MaxQty = 0` for any 429 row. Without an instrument-master cache, **every margin call also re-downloaded `scrip-master-detailed.csv`** (~218k rows from Dhan CDN) — that pattern appeared 12 times in a single browser refresh of 133 stocks, suggesting per-row (not per-process) cache layering.

**Why:** Dhan's `/margincalculator` rate limit is per-second-per-token. `https://api.dhan.co` master endpoints are slow (~2s each) and amplify the rate limit further.

**How to apply:** before shipping Auto-Buy (`POST /api/orders/place`'s `source='auto_buy'` path), introduce:
  1. A module-level `INSTRUMENT_DF_CACHE` in `broker/quantity_calculator.py` keyed off `(master_csv_url, max_age_seconds)` so the master loads once per process, not once per row.
  2. Throttling around `get_margin_per_share()` — ~10 req/s sleep, or use a shared `asyncio.Semaphore` per worker.
  3. A retry-with-backoff on 429 before zeroing `MaxQty`, and surface a row-level status (`status: "rate_limited"` vs `status: "ok"`) on the response so the UI can badge it instead of silently showing 0.
