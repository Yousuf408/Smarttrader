# ═══════════════════════════════════════════════════════════════════════════════
# TV SCREENER — QUANTITY CALCULATOR MODULE
#
# Calculates how many shares of each stock can be bought given the user's
# total capital, split into N parts (default 4), using DhanHQ's live
# Margin Calculator API (real intraday margin per stock, not a hardcoded
# 5x assumption — some stocks have different leverage e.g. 2x).
#
# Flow:
#   1. Load NSE-equity symbol -> securityId map (from Dhan's public
#      instrument master CSV, cached for 24h)
#   2. For each stock in the table, call Margin Calculator API with
#      quantity=1, productType=INTRADAY to get margin required per share
#   3. max_quantity = floor(capital_per_part / margin_per_share)
#
# DEBUG: every failure reason is tracked in st.session_state['qty_calc_debug']
# so issues (bad token, symbol not found, API error) are visible, not silent.
# ═══════════════════════════════════════════════════════════════════════════════

import streamlit as st
import pandas as pd
import requests
import io
import math
import pyotp
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: DHAN CREDENTIALS
#
# DHAN_ACCESS_TOKEN is NO LONGER used directly — access tokens are now
# auto-generated daily via TOTP (see get_access_token() below), since Dhan
# access tokens expire every 24 hours and manual regeneration is impractical.
# ─────────────────────────────────────────────────────────────────────────────

DHAN_CLIENT_ID = "1102302753"
DHAN_PIN = "786786"                  # 4/6-digit trading PIN
DHAN_TOTP_SECRET = "THWBRO5KI5N7ACJUNY7W3JUDKL4M2LML"  # From Profile > DhanHQ Trading APIs > Set-up TOTP

# TEMPORARY/BACKUP MANUAL OVERRIDE — paste a token generated via Dhan
# dashboard's "Generate new Access Token" button here to bypass the TOTP
# flow (e.g. while it's rate-limited). Leave as "" to use automatic TOTP.
#
# IMPORTANT: For Order Placement to work (IP-whitelist required), this
# manual token MUST be generated while your BROWSER traffic is itself
# routed through the same proxy (151.242.178.149:50100) — e.g. via a
# FoxyProxy extension — so the token is bound to the whitelisted IP.
# If generated from your normal browser/system IP, it will work fine for
# Margin Calculator / Fund Limit (no IP-whitelist needed there) but will
# FAIL with "Invalid IP" on Order Placement specifically.
DHAN_MANUAL_ACCESS_TOKEN = ""

DHAN_MARGIN_CALCULATOR_URL = "https://api.dhan.co/v2/margincalculator"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"
DHAN_TOKEN_GENERATE_URL = "https://auth.dhan.co/app/generateAccessToken"
DHAN_FUND_LIMIT_URL = "https://api.dhan.co/v2/fundlimit"

# Token generation MUST come from the same whitelisted static IP used for
# order placement — Dhan's own guidance: "if IPs has been added post
# [token generation], regenerate access token" — so route this call
# through the same proxy as dhan_orders.py to keep the source IP consistent.
DHAN_PROXY_HOST = "151.242.178.149"
DHAN_PROXY_PORT = "50100"
DHAN_PROXY_USERNAME = "yousufshaikh420"
DHAN_PROXY_PASSWORD = "cVTbJi6VVA"
DHAN_PROXY_URL = f"http://{DHAN_PROXY_USERNAME}:{DHAN_PROXY_PASSWORD}@{DHAN_PROXY_HOST}:{DHAN_PROXY_PORT}"
DHAN_PROXIES = {"http": DHAN_PROXY_URL, "https": DHAN_PROXY_URL}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: SUPABASE TOKEN HELPERS (aaj ki date ka token save/fetch)
#
# Sirf 2 functions — save aur fetch. Baaki koi Supabase logic nahi.
# Table: dhan_tokens (token_date date UNIQUE, access_token text)
# ─────────────────────────────────────────────────────────────────────────────

def _supabase_save_token(token):
    """
    Save today's Dhan access token to Supabase dhan_tokens table.
    Uses upsert so re-pasting same day overwrites cleanly.
    Uses already-initialized supabase object from database.py.
    """
    try:
        from tv_screener.database import supabase
        today_str = datetime.now().date().isoformat()
        supabase.table("dhan_tokens").upsert(
            {"token_date": today_str, "access_token": token},
            on_conflict="token_date"
        ).execute()
    except Exception as e:
        _log_debug('token_error', f"Supabase token save failed: {str(e)}")


def _supabase_fetch_token():
    """
    Fetch today's Dhan access token from Supabase dhan_tokens table.
    Returns token string if found for today's date, else None.
    Uses already-initialized supabase object from database.py.
    """
    try:
        from tv_screener.database import supabase
        today_str = datetime.now().date().isoformat()
        result = supabase.table("dhan_tokens").select("access_token").eq("token_date", today_str).execute()
        if result.data and len(result.data) > 0:
            return result.data[0].get("access_token")
        return None
    except Exception as e:
        _log_debug('token_error', f"Supabase token fetch failed: {str(e)}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# SECTION: AUTO ACCESS TOKEN GENERATION (via TOTP — no manual daily login)
# ─────────────────────────────────────────────────────────────────────────────

def _generate_totp_code():
    """Generate the current 6-digit TOTP code from the stored secret."""
    return pyotp.TOTP(DHAN_TOTP_SECRET).now()


def get_access_token(force_refresh=False):
    """
    Return a valid DhanHQ access token, auto-generating a fresh one via
    TOTP if the cached token is missing or expired.

    Priority order:
      1. UI-entered token (session_state box) — also saved to Supabase
      2. Supabase — aaj ki date ka token (persists across page refreshes)
      3. session_state cache (same session, not yet expired)
      4. Hardcoded DHAN_MANUAL_ACCESS_TOKEN (backward-compat fallback)
      5. TOTP auto-generate (slowest, rate-limit risk)

    Args:
        force_refresh (bool): If True, skip session cache and re-fetch

    Returns:
        str: Valid access token, or None if all methods failed
    """
    now = datetime.now()

    # ── Priority 1: UI box — user ne paste kiya ──
    # Immediately save to Supabase so page refresh pe bhi available rahe
    ui_token = st.session_state.get('user_manual_access_token', '').strip()
    if ui_token:
        # Save to Supabase (silent fail — never blocks)
        _supabase_save_token(ui_token)
        # Cache in session too
        st.session_state['dhan_access_token_data'] = {
            "token": ui_token,
            "expiry": now + timedelta(hours=23),
        }
        _log_debug('token_error', None)
        _log_debug('token_last_generated', "UI OVERRIDE (pasted in app by user)")
        return ui_token

    # ── Priority 2: Supabase — aaj ki date ka token ──
    # Page refresh ke baad bhi kaam karega (session clear hone par bhi)
    if not force_refresh:
        supabase_token = _supabase_fetch_token()
        if supabase_token:
            st.session_state['dhan_access_token_data'] = {
                "token": supabase_token,
                "expiry": now + timedelta(hours=23),
            }
            _log_debug('token_error', None)
            _log_debug('token_last_generated', "SUPABASE (aaj ki date ka saved token)")
            return supabase_token

    # ── Priority 3: Session cache (same session, not expired) ──
    cached = st.session_state.get('dhan_access_token_data')
    if not force_refresh and cached and cached.get('expiry') and cached['expiry'] > now:
        return cached['token']

    # ── Priority 4: Hardcoded manual override (backward-compat) ──
    if DHAN_MANUAL_ACCESS_TOKEN:
        st.session_state['dhan_access_token_data'] = {
            "token": DHAN_MANUAL_ACCESS_TOKEN,
            "expiry": now + timedelta(hours=23),
        }
        _log_debug('token_error', None)
        _log_debug('token_last_generated', "MANUAL OVERRIDE (pasted from Dhan dashboard)")
        return DHAN_MANUAL_ACCESS_TOKEN

    # ── Priority 5: TOTP auto-generate (fallback) ──
    try:
        totp_code = _generate_totp_code()
        params = {
            "dhanClientId": DHAN_CLIENT_ID,
            "pin": DHAN_PIN,
            "totp": totp_code,
        }
        response = requests.post(DHAN_TOKEN_GENERATE_URL, params=params, proxies=DHAN_PROXIES, timeout=10)

        if response.status_code != 200:
            _log_debug('token_error', f"HTTP {response.status_code}: {response.text[:200]}")
            return None

        data = response.json()
        token = data.get("accessToken")
        if not token:
            _log_debug('token_error', f"No accessToken in response: {data}")
            return None

        expiry_str = data.get("expiryTime")
        try:
            expiry = datetime.fromisoformat(expiry_str) if expiry_str else now + timedelta(hours=23)
        except Exception:
            expiry = now + timedelta(hours=23)

        # Keep a 5-minute safety buffer before actual expiry
        st.session_state['dhan_access_token_data'] = {
            "token": token,
            "expiry": expiry - timedelta(minutes=5),
        }
        # Also save TOTP-generated token to Supabase for next refresh
        _supabase_save_token(token)

        _log_debug('token_error', None)
        _log_debug('token_last_generated', now.strftime("%Y-%m-%d %H:%M:%S") + " (TOTP)")
        return token
    except Exception as e:
        _log_debug('token_error', f"Exception: {str(e)}")
        return None

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: DEBUG TRACKING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _init_debug():
    if 'qty_calc_debug' not in st.session_state:
        st.session_state['qty_calc_debug'] = {
            'security_map_size': 0,
            'security_map_columns_found': None,
            'security_map_error': None,
            'token_error': None,
            'token_last_generated': None,
            'per_symbol': {},  # symbol -> {"security_id": ..., "margin_error": ...}
        }

def _log_debug(key, value):
    _init_debug()
    st.session_state['qty_calc_debug'][key] = value

def _log_symbol_debug(symbol, **kwargs):
    _init_debug()
    if symbol not in st.session_state['qty_calc_debug']['per_symbol']:
        st.session_state['qty_calc_debug']['per_symbol'][symbol] = {}
    st.session_state['qty_calc_debug']['per_symbol'][symbol].update(kwargs)

def get_qty_calc_debug():
    """Return the debug info dict for display in a UI expander."""
    _init_debug()
    return st.session_state['qty_calc_debug']

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: FETCH AVAILABLE BALANCE DIRECTLY FROM BROKER (Fund Limit API)
# ─────────────────────────────────────────────────────────────────────────────

def get_available_balance():
    """
    Fetch current available trading balance directly from DhanHQ account
    (Fund Limit API) — so the user doesn't have to manually type capital.

    Returns:
        tuple: (balance: float or None, error_message: str or None)
    """
    access_token = get_access_token()
    if not access_token:
        return None, "Could not obtain access token (check TOTP/PIN/client_id)"

    try:
        headers = {
            "Content-Type": "application/json",
            "access-token": access_token,
        }
        response = requests.get(DHAN_FUND_LIMIT_URL, headers=headers, proxies=DHAN_PROXIES, timeout=10)

        if response.status_code == 401:
            access_token = get_access_token(force_refresh=True)
            if not access_token:
                return None, "401 Unauthorized, and token refresh also failed"
            headers["access-token"] = access_token
            response = requests.get(DHAN_FUND_LIMIT_URL, headers=headers, proxies=DHAN_PROXIES, timeout=10)

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:200]}"

        data = response.json()
        # Note: Dhan's API has a typo in this field name — "availabelBalance"
        balance = data.get("availabelBalance", data.get("availableBalance"))
        if balance is None:
            return None, f"No balance field in response: {data}"
        return float(balance), None
    except Exception as e:
        return None, f"Exception: {str(e)}"

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: SYMBOL -> SECURITY ID MAP (Dhan's public instrument master)
# ─────────────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=86400, show_spinner=False)
def get_security_id_map():
    """
    Download Dhan's public instrument master CSV and build a
    {trading_symbol: security_id} map for NSE Equity instruments.

    Cached for 24 hours — the instrument master rarely changes intraday,
    so no need to re-download on every rerun.

    Returns:
        dict: {symbol: security_id_str}, or {} if download/parse failed
              (check get_qty_calc_debug() for the failure reason)
    """
    try:
        response = requests.get(DHAN_SCRIP_MASTER_URL, timeout=30)
        if response.status_code != 200:
            _log_debug('security_map_error', f"CSV download failed: HTTP {response.status_code}")
            return {}

        df = pd.read_csv(io.StringIO(response.text), low_memory=False)

        # CORRECTED: UNDERLYING_SYMBOL is confirmed (via Dhan tooling docs)
        # to hold the PLAIN trading symbol for equity-cash rows (e.g.
        # "RELIANCE", "TCS") — NOT SYMBOL_NAME, which holds a different
        # (longer/company-name style) format that doesn't match TradingView
        # symbols. Priority: UNDERLYING_SYMBOL first for equities.
        symbol_col = None
        for candidate_name in ("UNDERLYING_SYMBOL", "TRADING_SYMBOL", "SYMBOL_NAME"):
            match = next((c for c in df.columns if c.upper() == candidate_name), None)
            if match:
                symbol_col = match
                break

        # Same explicit-priority principle for security_id_col — prefer an
        # EXACT "SECURITY_ID" match over "UNDERLYING_SECURITY_ID" (which
        # also contains the substring "SECURITY_ID"), regardless of order.
        security_id_col = next((c for c in df.columns if c.upper() == "SECURITY_ID"), None)
        if not security_id_col:
            security_id_col = next((c for c in df.columns if "SECURITY_ID" in c.upper()), None)

        series_col = next((c for c in df.columns if c.upper() == "SERIES"), None)
        exch_col = next((c for c in df.columns if c.upper() in ("SEM_EXM_EXCH_ID", "EXCH_ID")), None)
        segment_col = next((c for c in df.columns if c.upper() == "SEGMENT"), None)
        instrument_col = next((c for c in df.columns if "INSTRUMENT" in c.upper() and "EXCH" not in c.upper() and "UNDERLYING" not in c.upper()), None)

        _log_debug('security_map_columns_found', {
            'all_columns': list(df.columns),
            'symbol_col': symbol_col,
            'security_id_col': security_id_col,
            'exch_col': exch_col,
            'segment_col': segment_col,
            'instrument_col': instrument_col,
        })

        if not symbol_col or not security_id_col:
            _log_debug('security_map_error', "Could not find symbol/security_id columns in CSV")
            return {}

        # Filter to NSE Equity only (avoid F&O/currency/commodity duplicates
        # of the same symbol name). Equity cash rows have SEGMENT == "E".
        filtered = df
        if exch_col:
            filtered = filtered[filtered[exch_col].astype(str).str.upper() == "NSE"]
        if segment_col:
            filtered = filtered[filtered[segment_col].astype(str).str.upper() == "E"]
        elif instrument_col:
            filtered = filtered[filtered[instrument_col].astype(str).str.upper().isin(["EQUITY", "ES"])]

        security_map = {}
        symbol_series_seen = {}  # symbol -> series value of the currently-stored entry
        duplicate_symbols = {}   # symbol -> list of all (security_id, series) candidates seen

        for _, row in filtered.iterrows():
            sym = str(row[symbol_col]).strip().upper()
            sec_id = str(row[security_id_col]).strip()
            series_val = str(row[series_col]).strip().upper() if series_col else None
            if not sym or not sec_id:
                continue

            if sym not in security_map:
                security_map[sym] = sec_id
                symbol_series_seen[sym] = series_val
                if series_col:
                    duplicate_symbols[sym] = [(sec_id, series_val)]
            else:
                # Duplicate symbol found — track it, and prefer "EQ" series
                # (standard equity series) over whatever we already stored,
                # since duplicates are usually EQ vs some other series/type.
                if series_col:
                    duplicate_symbols.setdefault(sym, []).append((sec_id, series_val))
                    if series_val == "EQ" and symbol_series_seen.get(sym) != "EQ":
                        security_map[sym] = sec_id
                        symbol_series_seen[sym] = series_val

        # Only keep symbols that actually had duplicates, capped to a
        # reasonable sample size for the debug view
        duplicate_symbols = {k: v for k, v in duplicate_symbols.items() if len(v) > 1}
        _log_debug('duplicate_symbols_sample', dict(list(duplicate_symbols.items())[:30]))
        _log_debug('duplicate_symbols_count', len(duplicate_symbols))

        _log_debug('security_map_size', len(security_map))
        if len(security_map) == 0:
            _log_debug('security_map_error', "Filtered result was empty — check exch/instrument filter values")

        return security_map
    except Exception as e:
        _log_debug('security_map_error', f"Exception: {str(e)}")
        return {}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: MARGIN CALCULATOR API CALL (per stock)
# ─────────────────────────────────────────────────────────────────────────────

def get_margin_per_share(security_id, price, product_type="INTRADAY"):
    """
    Call DhanHQ's live Margin Calculator API to get the real margin
    required to buy 1 share of a stock (accounts for actual leverage —
    most stocks are 5x, some are 2x or other custom ratios).

    Uses get_access_token() internally — token is auto-generated/refreshed
    via TOTP, no manual daily login needed.

    Args:
        security_id (str): Dhan's internal security ID for the symbol
        price (float): Current market price of the stock
        product_type (str): "INTRADAY" for MIS margin (leveraged),
                            "CNC" for full delivery margin (no leverage)

    Returns:
        tuple: (margin_per_share: float or None, error_message: str or None)
    """
    access_token = get_access_token()
    if not access_token:
        return None, "Could not obtain access token (check TOTP/PIN/client_id)"

    try:
        payload = {
            "dhanClientId": str(DHAN_CLIENT_ID),
            "exchangeSegment": "NSE_EQ",
            "transactionType": "BUY",
            "quantity": 1,
            "productType": product_type,
            "securityId": str(security_id),
            "price": float(price),
            "triggerPrice": 0,
        }
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "access-token": access_token,
        }
        response = requests.post(DHAN_MARGIN_CALCULATOR_URL, json=payload, headers=headers, proxies=DHAN_PROXIES, timeout=10)

        if response.status_code == 401:
            # Token might have just expired — force a one-time refresh and retry
            access_token = get_access_token(force_refresh=True)
            if not access_token:
                return None, "401 Unauthorized, and token refresh also failed"
            headers["access-token"] = access_token
            response = requests.post(DHAN_MARGIN_CALCULATOR_URL, json=payload, headers=headers, proxies=DHAN_PROXIES, timeout=10)

        if response.status_code != 200:
            return None, f"HTTP {response.status_code}: {response.text[:200]}"

        data = response.json()
        margin = data.get("totalMargin")
        if margin is None:
            return None, f"No totalMargin in response: {data}"
        return float(margin), None
    except Exception as e:
        return None, f"Exception: {str(e)}"

# ─────────────────────────────────────────────────────────────────────────────
# SECTION: MAX QUANTITY CALCULATION (main entry point)
# ─────────────────────────────────────────────────────────────────────────────

def _supabase_fetch_all_margins(signal_date_str):
    """
    Aaj ki date ke saare stocks ka intraday_margin ek Supabase call mein fetch karo.
    Returns dict: {symbol: intraday_margin float}
    """
    try:
        from tv_screener.database import supabase
        result = (supabase.table("tv_screener_cache")
                  .select("symbol, intraday_margin")
                  .eq("signal_date", signal_date_str)
                  .execute())
        if result.data:
            return {
                row['symbol']: float(row['intraday_margin'])
                for row in result.data
                if row.get('intraday_margin') is not None
            }
        return {}
    except Exception as e:
        _log_debug('token_error', f"Supabase margin fetch failed: {str(e)}")
        return {}


def _supabase_save_margin(symbol, signal_date_str, margin_value):
    """
    Ek stock ka intraday_margin Supabase mein save karo (upsert).
    signal_date = aaj ki date, calc_date = kal ki date (table constraint).
    calc_date NOT NULL hai isliye dono dena zaroori hai — warna insert fail.
    Silent fail — margin save failure should never block trading.
    """
    try:
        from tv_screener.database import supabase
        from datetime import date
        calc_date_str = (date.fromisoformat(signal_date_str) - timedelta(days=1)).isoformat()
        supabase.table("tv_screener_cache").upsert(
            {
                "symbol":         symbol,
                "signal_date":    signal_date_str,
                "calc_date":      calc_date_str,
                "intraday_margin": margin_value,
            },
            on_conflict="symbol,signal_date"
        ).execute()
    except Exception:
        pass  # Silent fail


def calculate_max_quantity_column(df, total_capital, num_parts=4):
    """
    For each stock in df, calculate the max quantity purchasable using
    (total_capital / num_parts) as the budget for that single stock,
    accounting for real intraday margin/leverage via DhanHQ.

    Cache priority (fastest to slowest):
      1. session_state margin_cache  — same session, instant
      2. Supabase intraday_margin    — persists across page refreshes
      3. Dhan Margin API (PARALLEL)  — 10 threads, ~10x faster than serial

    Args:
        df (pd.DataFrame): Must have 'Symbol' and 'Price' columns
        total_capital (float): User's total trading capital
        num_parts (int): Number of equal parts to split capital into (default 4)

    Returns:
        pd.Series: Max quantity per row (int), aligned with df's index.
                   0 if margin couldn't be determined for that symbol.
                   Check get_qty_calc_debug() to see why, per symbol.
    """
    _init_debug()

    if 'margin_cache' not in st.session_state:
        st.session_state['margin_cache'] = {}  # symbol -> margin_per_share (float)

    if total_capital is None or total_capital <= 0 or df.empty:
        return pd.Series([0] * len(df), index=df.index)

    part_capital = total_capital / num_parts
    security_map = get_security_id_map()
    today_str = datetime.now().date().isoformat()

    # ── Step 1: Supabase se aaj ki date ka saara margin ek call mein fetch karo ──
    symbols_in_df = [str(r.get("Symbol", "")).strip().upper() for _, r in df.iterrows()]
    missing_from_session = [s for s in symbols_in_df if s not in st.session_state['margin_cache']]

    if missing_from_session:
        sb_margins = _supabase_fetch_all_margins(today_str)
        for sym, margin_val in sb_margins.items():
            if sym not in st.session_state['margin_cache']:
                st.session_state['margin_cache'][sym] = margin_val

    # ── Step 2: Jo stocks abhi bhi missing hain — parallel Dhan API calls ──
    # Price lookup for parallel fetch
    price_lookup = {
        str(r.get("Symbol", "")).strip().upper(): r.get("Price", 0)
        for _, r in df.iterrows()
    }

    still_missing = [
        s for s in symbols_in_df
        if s not in st.session_state['margin_cache']
        and security_map.get(s)
        and price_lookup.get(s, 0) > 0
    ]

    def fetch_margin_for_symbol(symbol):
        """Single symbol margin fetch — runs in thread pool."""
        security_id = security_map.get(symbol)
        price = price_lookup.get(symbol, 0)
        margin, error = get_margin_per_share(security_id, price)
        return symbol, security_id, margin, error

    if still_missing:
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {executor.submit(fetch_margin_for_symbol, sym): sym for sym in still_missing}
            for future in as_completed(futures):
                sym, sec_id, margin_val, error = future.result()
                _log_symbol_debug(sym, security_id=sec_id, margin_error=error, margin_value=margin_val)
                if margin_val is not None and margin_val > 0:
                    # Session cache mein save
                    st.session_state['margin_cache'][sym] = margin_val
                    # Supabase mein bhi save — next refresh pe no API call
                    _supabase_save_margin(sym, today_str, margin_val)

    # ── Step 3: Qty calculate karo — sab kuch ab session cache mein hai ──
    max_qty_list = []
    for _, row in df.iterrows():
        symbol = str(row.get("Symbol", "")).strip().upper()
        price  = row.get("Price", 0)

        # Log symbols not found in security map
        if not security_map.get(symbol):
            _log_symbol_debug(symbol, security_id=None, margin_error="Symbol not found in Dhan instrument master")

        margin_per_share = st.session_state['margin_cache'].get(symbol)

        if margin_per_share is None or margin_per_share <= 0:
            max_qty_list.append(0)
            continue

        max_qty = math.floor(part_capital / margin_per_share)
        max_qty_list.append(max(max_qty, 0))

    return pd.Series(max_qty_list, index=df.index)
