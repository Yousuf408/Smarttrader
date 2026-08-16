"""Authenticated TradingView 5-minute candle backfill for Advance ORB.

This deliberately uses the chart feed, not the TradingView scanner's daily bar.
Credentials are read only from Replit Secrets/environment.
"""
from __future__ import annotations

import datetime as dt
import asyncio
import json
import logging
import os
import random
import re
import string
import threading
import time
from typing import Any

import requests

try:
    import websockets
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise RuntimeError(
        "Missing dependency: install the `websockets` package required by the "
        "TradingView OHLC fetcher (pip install websockets)."
    ) from exc

logger = logging.getLogger("advance_orb.tv_chart")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_TV_TOKEN_CACHE: tuple[float, str] | None = None
_TV_CANDLE_CACHE: dict[
    tuple[str, int, str],
    tuple[float, tuple[float, float, float, float, float | None]],
] = {}
# Separate cache for confirmed second-candle closes (only written after the
# 2nd candle's close time, so it never holds a provisional forming-bar value).
_TV_C2_CACHE: dict[
    tuple[str, int, str],          # (date, timeframe, symbol)
    tuple[float, float],           # (stored_at, c2_close)
] = {}
_TV_C2_CACHE_TTL = 20 * 60 * 60  # 20 h — full trading session
# Cache for finalized per-bar OHLC (keyed by date, timeframe, symbol, bar_label).
# Only written after the bar has closed so it never holds a forming-bar value.
_TV_BAR_OHLC_CACHE: dict[
    tuple[str, int, str, str],     # (date, timeframe, symbol, bar_label "HHMM")
    tuple[float, tuple[float, float, float, float]],  # (stored_at, (o, h, l, c))
] = {}
_TV_BAR_OHLC_CACHE_TTL = 20 * 60 * 60  # 20 h — full trading session
_TV_CACHE_LOCK = threading.Lock()
_TV_TOKEN_TTL = 10 * 60
# TradingView throttles rapid logins (captcha / "having a little trouble").
# After a failed login, do NOT retry for a cooldown window so the 30-second
# auto-refresh loop doesn't hammer the sign-in endpoint and worsen the block.
_TV_LOGIN_COOLDOWN_S = 5 * 60
_TV_LOGIN_FAIL_AT: float | None = None
# Persist the failure timestamp across process restarts: a server restart
# would otherwise immediately fire another login attempt and extend the block.
_TV_FAIL_MARKER = os.path.join(
    __import__("tempfile").gettempdir(), "advance_orb_tv_login_fail.ts"
)


def _read_fail_marker() -> float | None:
    try:
        with open(_TV_FAIL_MARKER, "r") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def _write_fail_marker(ts: float) -> None:
    try:
        tmp = _TV_FAIL_MARKER + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(str(ts))
        os.replace(tmp, _TV_FAIL_MARKER)
    except OSError:
        pass


def _mark_login_fail(now_ts: float) -> None:
    global _TV_LOGIN_FAIL_AT
    with _TV_CACHE_LOCK:
        _TV_LOGIN_FAIL_AT = now_ts
    _write_fail_marker(now_ts)
# These values are immutable for the trading session: yesterday's high and
# the completed 09:15-09:20 candle do not change after they are published.
# Keep them in process memory for the rest of the session so 30-second UI
# refreshes never re-query the chart feed.
_TV_CANDLE_TTL = 20 * 60 * 60


def _frame(method: str, params: list[Any]) -> str:
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    return f"~m~{len(payload)}~m~{payload}"


def _id(prefix: str) -> str:
    return prefix + "".join(random.choice(string.ascii_lowercase) for _ in range(12))


def _iter_payload_objects(raw: str):
    """Yield JSON objects from TradingView's framed websocket payloads."""
    candidates = re.findall(r"~m~\d+~m~(.*?)(?=~m~\d+~m~|$)", raw, flags=re.S)
    for candidate in candidates:
        text = candidate.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        yield payload


def _walk_series_payload(value):
    """Recursively extract the `s` series from nested TradingView payloads."""
    if isinstance(value, dict):
        if "s" in value and isinstance(value["s"], list):
            series = value["s"]
            if series and isinstance(series[0], list):
                rows: list[list[float]] = []
                for row in series:
                    if not isinstance(row, (list, tuple)) or len(row) < 6:
                        continue
                    try:
                        rows.append([float(v) for v in row[:6]])
                    except (TypeError, ValueError):
                        continue
                if rows:
                    return rows
        for child in value.values():
            rows = _walk_series_payload(child)
            if rows:
                return rows
    elif isinstance(value, list):
        for child in value:
            rows = _walk_series_payload(child)
            if rows:
                return rows
    return []


def _series_rows(raw: str) -> list[list[float]]:
    """Parse the series payload format used by the TV chart websocket.

    TradingView sends framed JSON messages (``~m~len~m~{...}``) and the OHLC
    series is nested under the key ``"s"``.  A regex-based split on commas and
    brackets is brittle and often misreads the payload after the next trading day
    when the message structure changes slightly.  We decode the message payloads
    and walk the nested object tree instead, which is stable across TV updates.
    """
    rows: list[list[float]] = []
    for payload in _iter_payload_objects(raw):
        found = _walk_series_payload(payload)
        if found:
            rows.extend(found)
    return rows


def _first_candle(rows: list[list[float]]) -> tuple[float, float, float, float] | None:
    today = dt.datetime.now(IST).date()
    for row in sorted(rows, key=lambda x: x[0]):
        stamp = dt.datetime.fromtimestamp(row[0], IST)
        if stamp.date() == today and stamp.hour == 9 and stamp.minute == 15:
            return row[1], row[2], row[3], row[4]
    return None


def _candle_close_at(
    rows: list[list[float]], hour: int, minute: int
) -> float | None:
    """Return the TRUE (finalized) close of the bar that opens at (hour:minute) IST.

    This reads directly from the authenticated TV chart series, which contains
    completed historical bars — not a forming-bar snapshot.  The result is only
    valid after the bar has closed; callers must check wall-clock time before
    relying on it.
    """
    today = dt.datetime.now(IST).date()
    for row in sorted(rows, key=lambda x: x[0]):
        stamp = dt.datetime.fromtimestamp(row[0], IST)
        if stamp.date() == today and stamp.hour == hour and stamp.minute == minute:
            return row[4]  # index 4 = close (tvDatafeed layout: ts,o,h,l,c,v)
    return None


def _candle_ohlc_at(
    rows: list[list[float]], hour: int, minute: int
) -> tuple[float, float, float, float] | None:
    """Return the TRUE (finalized) OHLC of the bar that opens at (hour:minute) IST.

    Layout from ``_series_rows``: [timestamp, open, high, low, close, volume].
    Only valid after the bar has closed; callers must check wall-clock time.
    """
    today = dt.datetime.now(IST).date()
    for row in sorted(rows, key=lambda x: x[0]):
        stamp = dt.datetime.fromtimestamp(row[0], IST)
        if stamp.date() == today and stamp.hour == hour and stamp.minute == minute:
            return row[1], row[2], row[3], row[4]  # o, h, l, c
    return None


def _yesterday_high(rows: list[list[float]]) -> float | None:
    """Max 5-min bar high of the previous trading day (= that day's daily
    high on the daily timeframe) — computed from TradingView's own bars."""
    today = dt.datetime.now(IST).date()
    day_highs: dict[dt.date, float] = {}
    for row in sorted(rows, key=lambda x: x[0]):
        stamp = dt.datetime.fromtimestamp(row[0], IST)
        if stamp.date() >= today:
            continue
        day_highs[stamp.date()] = max(day_highs.get(stamp.date(), 0.0), row[2])
    if not day_highs:
        return None
    prev_day = max(day_highs)  # most recent completed session before today
    return day_highs[prev_day]


async def _batch_tv_opening_candles_async(
    result: dict[str, tuple[float, float, float, float] | None],
    token: str,
    timeframe: int = 5,
) -> None:
    chart = _id("cs_")
    quote = _id("qs_")
    async with websockets.connect(
        "wss://data.tradingview.com/socket.io/websocket",
        origin="https://data.tradingview.com",
        ping_interval=None,
        max_size=2**26,
    ) as ws:
        await ws.send(_frame("set_auth_token", [token]))
        await ws.send(_frame("chart_create_session", [chart, ""]))
        await ws.send(_frame("quote_create_session", [quote]))
        for index, symbol in enumerate(result):
            tv_symbol = f"NSE:{symbol.removesuffix('.NS')}"
            alias = f"symbol_{index}"
            series = f"s{index}"
            await ws.send(_frame("resolve_symbol", [
                chart, alias,
                f'={{"symbol":"{tv_symbol}","adjustment":"splits","session":"regular"}}',
            ]))
            await ws.send(_frame("create_series", [chart, series, series, alias, f"{timeframe}", 500]))
            raw = ""
            while True:
                message = await ws.recv()
                raw += message
                if "series_completed" in message:
                    break
            rows = _series_rows(raw)
            candle = _first_candle(rows)
            if candle:
                result[symbol] = (*candle, _yesterday_high(rows))


async def _batch_tv_bar_ohlc_async(
    result: dict[str, tuple[float, float, float, float] | None],
    token: str,
    timeframe: int,
    bar_hour: int,
    bar_min: int,
) -> None:
    """Fetch the finalized OHLC of the bar opening at (bar_hour:bar_min) for each symbol.

    Uses the same authenticated TV chart feed as other batch functions.  Only
    called after the bar has closed, so the historical series always contains
    the final, authoritative value — never a forming-bar snapshot.
    """
    chart = _id("cs_")
    async with websockets.connect(
        "wss://data.tradingview.com/socket.io/websocket",
        origin="https://data.tradingview.com",
        ping_interval=None,
        max_size=2**26,
    ) as ws:
        await ws.send(_frame("set_auth_token", [token]))
        await ws.send(_frame("chart_create_session", [chart, ""]))
        for index, symbol in enumerate(result):
            tv_symbol = f"NSE:{symbol.removesuffix('.NS')}"
            alias = f"symbol_{index}"
            series = f"s{index}"
            await ws.send(_frame("resolve_symbol", [
                chart, alias,
                f'={{"symbol":"{tv_symbol}","adjustment":"splits","session":"regular"}}',
            ]))
            await ws.send(_frame("create_series", [chart, series, series, alias, f"{timeframe}", 10]))
            raw = ""
            while True:
                message = await ws.recv()
                raw += message
                if "series_completed" in message:
                    break
            rows = _series_rows(raw)
            ohlc = _candle_ohlc_at(rows, bar_hour, bar_min)
            if ohlc is not None:
                result[symbol] = ohlc


async def _batch_tv_c2_close_async(
    result: dict[str, float | None],
    token: str,
    timeframe: int,
    c2_hour: int,
    c2_min: int,
) -> None:
    """Fetch the confirmed close of the 2nd candle for each symbol.

    Uses the same authenticated TV chart feed as the opening-candle path but
    is a SEPARATE async function so the two caches stay independent and the
    2nd-candle close is NEVER mixed with the provisional opening-candle data.
    """
    chart = _id("cs_")
    async with websockets.connect(
        "wss://data.tradingview.com/socket.io/websocket",
        origin="https://data.tradingview.com",
        ping_interval=None,
        max_size=2**26,
    ) as ws:
        await ws.send(_frame("set_auth_token", [token]))
        await ws.send(_frame("chart_create_session", [chart, ""]))
        for index, symbol in enumerate(result):
            tv_symbol = f"NSE:{symbol.removesuffix('.NS')}"
            alias = f"symbol_{index}"
            series = f"s{index}"
            await ws.send(_frame("resolve_symbol", [
                chart, alias,
                f'={{"symbol":"{tv_symbol}","adjustment":"splits","session":"regular"}}',
            ]))
            await ws.send(_frame("create_series", [chart, series, series, alias, f"{timeframe}", 10]))
            raw = ""
            while True:
                message = await ws.recv()
                raw += message
                if "series_completed" in message:
                    break
            rows = _series_rows(raw)
            c2c = _candle_close_at(rows, c2_hour, c2_min)
            if c2c is not None:
                result[symbol] = c2c


def batch_tv_confirmed_bar_ohlc(
    symbols: list[str],
    timeframe: int,
    bar_hour: int,
    bar_min: int,
) -> dict[str, tuple[float, float, float, float] | None]:
    """Return the finalized OHLC for the bar opening at (bar_hour:bar_min) IST.

    Only runs when that bar has already closed (wall-clock > bar_open + timeframe).
    Returns ``{symbol: (open, high, low, close)}`` from the authenticated TV chart
    feed — never tvscreener forming-bar data.  Results are cached for the full
    trading session so repeated calls (every ~30 s poll) hit memory, not the network.

    ``timeframe`` is minutes (5 or 15).
    """
    timeframe = int(timeframe)
    bar_close_min = bar_hour * 60 + bar_min + timeframe
    now = dt.datetime.now(IST)
    now_min = now.hour * 60 + now.minute
    syms = [str(s).strip().upper() for s in symbols if s]
    result: dict[str, tuple[float, float, float, float] | None] = {s: None for s in syms}
    if now_min < bar_close_min:
        return result  # bar hasn't closed yet — never return provisional data

    bar_label = f"{bar_hour:02d}{bar_min:02d}"
    username = os.getenv("TRADINGVIEW_USERNAME", "").strip()
    password = os.getenv("TRADINGVIEW_PASSWORD", "")
    if not username or not password or not syms:
        return result

    today = now.strftime("%Y-%m-%d")
    now_ts = time.time()
    with _TV_CACHE_LOCK:
        missing_syms: list[str] = []
        for sym in syms:
            cached = _TV_BAR_OHLC_CACHE.get((today, timeframe, sym, bar_label))
            if cached and now_ts - cached[0] < _TV_BAR_OHLC_CACHE_TTL:
                result[sym] = cached[1]
            else:
                missing_syms.append(sym)
    if not missing_syms:
        return result

    global _TV_TOKEN_CACHE, _TV_LOGIN_FAIL_AT
    with _TV_CACHE_LOCK:
        token = (
            _TV_TOKEN_CACHE[1]
            if _TV_TOKEN_CACHE and now_ts - _TV_TOKEN_CACHE[0] < _TV_TOKEN_TTL
            else ""
        )
        fail_at = _TV_LOGIN_FAIL_AT if _TV_LOGIN_FAIL_AT is not None else _read_fail_marker()
        login_blocked = fail_at is not None and now_ts - fail_at < _TV_LOGIN_COOLDOWN_S
    if not token and login_blocked:
        logger.warning(
            "TradingView bar-OHLC skipped: throttled %.0fs ago — cooling down",
            now_ts - (fail_at or 0),
        )
        return result

    missing_result: dict[str, tuple[float, float, float, float] | None] = {s: None for s in missing_syms}
    try:
        login_attempted = False
        token_ok = bool(token)
        if not token:
            login_attempted = True
            login = requests.post(
                "https://www.tradingview.com/accounts/signin/",
                data={"username": username, "password": password, "remember": "on"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tradingview.com"},
                timeout=20,
            ).json()
            token = (login.get("user") or {}).get("auth_token", "")
            token_ok = bool(token)
            if not token:
                logger.warning(
                    "TradingView bar-OHLC login failed: %s",
                    login.get("error", "unknown error"),
                )
                _mark_login_fail(now_ts)
                return result
            with _TV_CACHE_LOCK:
                _TV_TOKEN_CACHE = (now_ts, token)

        asyncio.run(_batch_tv_bar_ohlc_async(missing_result, token, timeframe, bar_hour, bar_min))
        with _TV_CACHE_LOCK:
            for sym, ohlc in missing_result.items():
                if ohlc is not None:
                    _TV_BAR_OHLC_CACHE[(today, timeframe, sym, bar_label)] = (now_ts, ohlc)
                    result[sym] = ohlc
    except Exception as exc:
        logger.warning("TradingView bar-OHLC fetch (%02d:%02d, %dm) failed: %s",
                       bar_hour, bar_min, timeframe, exc)
        if login_attempted and not token_ok:
            _mark_login_fail(now_ts)
    return result


def batch_tv_opening_candles(
    symbols: list[str],
    timeframe: int = 5,
) -> dict[str, tuple[float, float, float, float, float | None] | None]:
    """Fetch today's exact opening bar + yesterday's daily high (TV only).

    `timeframe` is minutes (5 or 15).  On 5-min this is the 09:15–09:20 bar;
    on 15-min it is the 09:15–09:30 bar.  Returns a 5-tuple per symbol:
    (open, high, low, close, yesterday_high).  The last element is the
    previous trading day's high (max of that day's bar highs = daily-timeframe
    high).  All values come straight from the authenticated TradingView chart
    feed — never Yahoo or Angel One.
    """
    result = {str(s).strip().upper(): None for s in symbols if s}
    timeframe = int(timeframe)
    # Before the opening candle closes there is nothing valid to fetch.
    now = dt.datetime.now(IST)
    first_close_min = 9 * 60 + 15 + timeframe  # 9:20 for 5m, 9:30 for 15m
    if now.hour * 60 + now.minute < first_close_min:
        return result
    username = os.getenv("TRADINGVIEW_USERNAME", "").strip()
    password = os.getenv("TRADINGVIEW_PASSWORD", "")
    if not username or not password or not result:
        return result

    today = now.strftime("%Y-%m-%d")
    now_ts = time.time()
    with _TV_CACHE_LOCK:
        for symbol in list(result):
            cached = _TV_CANDLE_CACHE.get((today, timeframe, symbol))
            if cached and now_ts - cached[0] < _TV_CANDLE_TTL:
                result[symbol] = cached[1]
        missing = {symbol: None for symbol, value in result.items() if value is None}
    if not missing:
        return result

    global _TV_TOKEN_CACHE, _TV_LOGIN_FAIL_AT
    with _TV_CACHE_LOCK:
        token = (
            _TV_TOKEN_CACHE[1]
            if _TV_TOKEN_CACHE and now_ts - _TV_TOKEN_CACHE[0] < _TV_TOKEN_TTL
            else ""
        )
        fail_at = _TV_LOGIN_FAIL_AT if _TV_LOGIN_FAIL_AT is not None else _read_fail_marker()
        login_blocked = fail_at is not None and now_ts - fail_at < _TV_LOGIN_COOLDOWN_S
    if not token and login_blocked:
        logger.warning(
            "TradingView login skipped: throttled %.0fs ago — cooling down",
            now_ts - (fail_at or 0),
        )
        return result

    try:
        login_attempted = False
        token_ok = bool(token)
        if not token:
            login_attempted = True
            login = requests.post(
                "https://www.tradingview.com/accounts/signin/",
                data={"username": username, "password": password, "remember": "on"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tradingview.com"},
                timeout=20,
            ).json()
            token = (login.get("user") or {}).get("auth_token", "")
            token_ok = bool(token)
            if not token:
                logger.warning("TradingView login did not return auth_token: %s", login.get("error", "unknown error"))
                _mark_login_fail(now_ts)
                return result
            with _TV_CACHE_LOCK:
                _TV_TOKEN_CACHE = (now_ts, token)

        asyncio.run(_batch_tv_opening_candles_async(missing, token, timeframe))
        with _TV_CACHE_LOCK:
            for symbol, value in missing.items():
                if value is not None:
                    _TV_CANDLE_CACHE[(today, timeframe, symbol)] = (now_ts, value)
                    result[symbol] = value
    except Exception as exc:
        logger.warning("TradingView 5-minute candle fetch failed: %s", exc)
        if login_attempted and not token_ok:
            # Login itself failed (e.g. anti-bot HTML/non-JSON response) —
            # treat it as throttling so we cool down instead of re-hammering.
            _mark_login_fail(now_ts)
    return result


def batch_tv_confirmed_c2_close(
    symbols: list[str],
    timeframe: int = 5,
) -> dict[str, float | None]:
    """Return the CONFIRMED (finalized) close of the 2nd candle for each symbol.

    The 2nd candle is 09:20 on the 5-min frame and 09:30 on the 15-min frame.
    This function ONLY runs when that candle has already closed, so the TV chart
    historical series always contains the final, authoritative OHLC — never a
    provisional forming-bar value.

    Uses a SEPARATE cache (``_TV_C2_CACHE``) from ``batch_tv_opening_candles``
    so the two data contracts never interfere: opening-candle fetches can happen
    before the 2nd candle closes; confirmed-c2 fetches happen only after.

    Returns ``{symbol: float}`` for symbols where the confirmed close was found,
    ``{symbol: None}`` otherwise.  All values come from the authenticated TV
    chart feed — never Yahoo or Angel One.
    """
    timeframe = int(timeframe)
    # 2nd candle close times: 09:25 for 5-min, 09:45 for 15-min.
    c2_close_min = 9 * 60 + 15 + 2 * timeframe
    now = dt.datetime.now(IST)
    now_min = now.hour * 60 + now.minute
    if now_min < c2_close_min:
        # 2nd candle hasn't closed yet — never return provisional data.
        return {str(s).strip().upper(): None for s in symbols if s}

    c2_hour, c2_min = (9, 20) if timeframe == 5 else (9, 30)
    syms = [str(s).strip().upper() for s in symbols if s]
    result: dict[str, float | None] = {s: None for s in syms}

    username = os.getenv("TRADINGVIEW_USERNAME", "").strip()
    password = os.getenv("TRADINGVIEW_PASSWORD", "")
    if not username or not password or not syms:
        return result

    today = now.strftime("%Y-%m-%d")
    now_ts = time.time()
    # Serve from cache where available.
    with _TV_CACHE_LOCK:
        missing_syms = []
        for sym in syms:
            cached = _TV_C2_CACHE.get((today, timeframe, sym))
            if cached and now_ts - cached[0] < _TV_C2_CACHE_TTL:
                result[sym] = cached[1]
            else:
                missing_syms.append(sym)
    if not missing_syms:
        return result

    global _TV_TOKEN_CACHE, _TV_LOGIN_FAIL_AT
    with _TV_CACHE_LOCK:
        token = (
            _TV_TOKEN_CACHE[1]
            if _TV_TOKEN_CACHE and now_ts - _TV_TOKEN_CACHE[0] < _TV_TOKEN_TTL
            else ""
        )
        fail_at = _TV_LOGIN_FAIL_AT if _TV_LOGIN_FAIL_AT is not None else _read_fail_marker()
        login_blocked = fail_at is not None and now_ts - fail_at < _TV_LOGIN_COOLDOWN_S
    if not token and login_blocked:
        logger.warning(
            "TradingView c2-close skipped: throttled %.0fs ago — cooling down",
            now_ts - (fail_at or 0),
        )
        return result

    missing_result: dict[str, float | None] = {s: None for s in missing_syms}
    try:
        login_attempted = False
        token_ok = bool(token)
        if not token:
            login_attempted = True
            login = requests.post(
                "https://www.tradingview.com/accounts/signin/",
                data={"username": username, "password": password, "remember": "on"},
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tradingview.com"},
                timeout=20,
            ).json()
            token = (login.get("user") or {}).get("auth_token", "")
            token_ok = bool(token)
            if not token:
                logger.warning(
                    "TradingView c2-close login failed: %s",
                    login.get("error", "unknown error"),
                )
                _mark_login_fail(now_ts)
                return result
            with _TV_CACHE_LOCK:
                _TV_TOKEN_CACHE = (now_ts, token)

        asyncio.run(
            _batch_tv_c2_close_async(missing_result, token, timeframe, c2_hour, c2_min)
        )
        with _TV_CACHE_LOCK:
            for sym, c2c in missing_result.items():
                if c2c is not None:
                    _TV_C2_CACHE[(today, timeframe, sym)] = (now_ts, c2c)
                    result[sym] = c2c
    except Exception as exc:
        logger.warning("TradingView c2-close fetch failed: %s", exc)
        if login_attempted and not token_ok:
            _mark_login_fail(now_ts)
    return result