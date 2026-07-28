---
name: Angel One WAF & margin SDK fix
description: How Angel One auth and margin calls were rewritten to use SmartConnect SDK, avoiding WAF blocks
---

## Problem
Raw `requests.post()` to Angel One API endpoints gets WAF-blocked (TLS fingerprint / header mismatch) even when the proxy IP is whitelisted. The official `smartapi-python` SDK sends headers the WAF accepts.

## Solution
- **Auth**: Use `SmartConnect.generateSession(client_id, password, totp)` instead of raw HTTP POST.
- **Margin**: Use `SmartConnect.getMarginApi(payload)` instead of raw HTTP POST.
- **Proxy**: Set `smart_api.proxies = ANGEL_PROXIES` (with `s`). The SDK reads `self.proxies` internally.

## Critical details discovered through testing
1. **"Bearer " prefix bug**: The `jwtToken` from `generateSession()` response already includes `"Bearer "` prefix. The SDK's `_request` does `"Bearer {}".format(access_token)`. Passing the raw jwtToken → `setAccessToken(jwtToken)` produces a **double** `"Bearer Bearer eyJ..."` auth header → "Invalid Token" (AG8001). **Fix**: strip `"Bearer "` prefix before storing.
2. **Payload field names**: The margin batch API uses **camelCase** fields, not snake_case.
   - `product_type` → `productType`
   - `transaction_type` → `tradeType`
   - `ordertype` → `orderType`
   - `quantity` → `qty`
3. **Token lookup**: The OpenAPI scrip master has `exch_seg` column (not `exchange`). Equity symbols are stored as `SYMBOL-EQ` (e.g. `RELIANCE-EQ` with token `2885`), while plain `SYMBOL` entries have a different token. The margin API needs the Angel One internal token (from the `-EQ` entry). The `get_token()` function now searches `exch_seg` column and tries `symbol-EQ` as fallback.

## Why this is hard to debug
- WAF blocks never reach the API — you get "Request Rejected" at the HTTP layer without a JSON response.
- The SDK logs errors internally but doesn't raise exceptions for application-level failures (status: false).
- The margin API version at `apiconnect.angelone.in` has different field names than documented older versions.
