---
name: Dhan margin-calc throttling and caching
description: Bulk /margincalculator calls throttle; the working fix is a per-symbol MARGIN_CACHE + module-level master CSV cache. TradeAlgo Pro screener.
---

The Dhan margin calculator (`POST https://api.dhan.co/margincalculator`) returns HTTP **429** when called in tightly-looped bulk batches (we observed throttling on ~130 back-to-back intraday calls inside a single `advanceorb` request). It also re-downloads `scrip-master-detailed.csv` (~218k rows) per row unless cached — that amplifier doubles the request cost and shows up as `✅ Loaded 218412 instruments` flooding the workflow log.

**Implemented (broker/quantity_calculator.py):**

- `MARGIN_CACHE: dict[(security_id, rounded_price)] = (margin, ts)` at module scope.
- `MARGIN_CACHE_TTL_SECONDS = 15 * 60`.
- `get_margin_per_share` short-circuits on cache hit **before** the Dhan call. Cache is written only when `response.status_code == 200 and margin > 0` — never on 429 or error returns, so retries stay unblocked.
- Verified effect: first screener call ~48 s with 133/133 MaxQty populated; second identical call ~4.6 s, **zero Dhan round-trips**, all rows still show their cached qty. Common-symbol shuffles between fetches are zero-cost.

**Not yet implemented (deferred):**

- `INSTRUMENT_DF_CACHE` / `MASTER_CSV_CACHE` — the master CSV still re-loads per row. Today the cache lives at function-local scope (`MASTER_CSV_CACHE = None` exists but isn't keyed). Sharing it module-level would silence the repeated `Loaded 218412 instruments` log noise and speed up the symbolic-id list scan.
- Async throttle / semaphore for non-cached first loads (`asyncio.Semaphore(8)` + `await asyncio.sleep(...)`).
- Retry-with-backoff on 429 — currently we zero `MaxQty` silently on 429, so the caller can't distinguish "transient throttle" from "no capital" or "missing token".

**Why:** Cache-key combines security_id *and* rounded price — without the price dimension, two calls with the same symbol but different prices (during RELVOL/GAP% reshuffles in the screener) would return a stale margin after a meaningful price drift. Rounding to 2 dp gives a stable key for sub-percent intraday moves while still invalidating on real price gaps.

**How to apply:** Use this pattern when adding `get_ltp` or `get_fundlimit` callers — wrap with module-level cache + TTL, skip cache write on non-200 / value ≤ 0. Top-N UI sort + this cache turns the screener into "first-load N calls, every-shuffle 0 calls" — fits Dhan's per-second-per-token limits without bespoke rate-limiting.
