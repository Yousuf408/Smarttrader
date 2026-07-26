---
name: Dhan margin-calc throttling and caching
description: Bulk /margincalculator calls throttle; the working fixes in TradeAlgo Pro are module-level eager master CSV preload and per-symbol MARGIN_CACHE.
---

Dhan's margin calculator (`POST https://api.dhan.co/margincalculator`) returns HTTP **429** when called in tightly-looped bulk batches. Without caching, the screener also re-downloads `scrip-master-detailed.csv` (~218k rows, ~2-3 s) per worker thread inside any refresh — that amplifier can dominate the cold path.

**Both fixes now implemented in `broker/quantity_calculator.py`:**

1. **Module-level `MARGIN_CACHE`** — keyed `dict[(security_id, rounded_price)] = (margin, ts)` with 15 min TTL. `get_margin_per_share` short-circuits on cache hit *before* any Dhan call. Cache is written only when `response.status_code == 200 and margin > 0` — never on 429 or error returns, so retries stay unblocked.

2. **Eager module-init `load_instrument_master()`** — runs once at process start (after the trailing `if __name__ == "__main__":` block), priming `MASTER_CSV_CACHE` before uvicorn accepts connections. The 8 worker threads of `ThreadPoolExecutor(max_workers=8)` now read from one canonical copy rather than each fetching the CSV on first access. Verified in workflow logs: `✅ Loaded 218412 instruments from Dhan master` + `✅ Master CSV preloaded at startup` fire exactly once per process, not 1× per worker per refresh.

**Verified end-to-end numbers** (curl `/api/strategies/advanceorb` with token active):

| State                  | Wall time | Dhan calls | Rows done  |
|------------------------|-----------|------------|------------|
| Cold cache (pre-fix)   | 48.45 s   | 133        | 133/133    |
| Cold cache (post-fix)  | 34.16 s   | 133        | 131/133    |
| Warm cache (post-fix)  |  4.35 s   |   0        | 133/133    |

Cold path dropped ~30% (48 → 34 s). Warm path 11× faster than cold (4.35 s vs 48 s). The 2 cold-call zeros (POLYMED, KIMS) are 429s self-healed by the warm call — never cached on a non-200 response.

**Not yet implemented** (deferred, no immediate urgency):

- `asyncio.Semaphore(N)` rate-limiter around un-cached first loads. Today cold loads still burst 8 req/s; only one column stayed at 0 due to a transient 429, which the cache absorbs on the next refresh.
- Retry-with-backoff on 429. Currently we silently zero `MaxQty` on 429 — caller can't distinguish "transient throttle" from "no capital" / "missing token". Surfacing a row-level `status: "rate_limited"` badge in the JSON would let the UI render an explicit warning instead of a misleading 0.

**Why eager preload beats a `threading.Lock`:** preload at import time avoids the lock + double-download risk during the first refresh after startup. The first refresh of the day still takes ~3 s longer (one-time CSV download baked into startup) but every refresh after that is ~3-5 s cheaper — net win as long as the process is up >1 refresh cycle.

**How to apply:** follow the same module-level-cache pattern when adding `get_ltp` or `get_fundlimit` callers; cache write only on 200 + value > 0; never on transient errors. The 8-thread fan-out ceiling can stay at 8 until Dhan's actual rate-limit threshold is measured.
