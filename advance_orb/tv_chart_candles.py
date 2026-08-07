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
import websockets

logger = logging.getLogger("advance_orb.tv_chart")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
_TV_TOKEN_CACHE: tuple[float, str] | None = None
_TV_CANDLE_CACHE: dict[tuple[str, str], tuple[float, tuple[float, float, float, float, float | None]] | None] = {}
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


def _series_rows(raw: str) -> list[list[float]]:
    """Parse the series payload format used by the TV chart websocket."""
    match = re.search(r'"s":\[(.+?)\}\]', raw, re.S)
    if not match:
        return []
    rows: list[list[float]] = []
    for item in match.group(1).split(',{"'):
        parts = re.split(r"\[|:|,|\]", item)
        try:
            # tvDatafeed's stable payload indexes: timestamp, OHLCV.
            values = [float(parts[i]) for i in range(4, 10)]
            rows.append(values)
        except (ValueError, IndexError):
            continue
    return rows


def _first_candle(rows: list[list[float]]) -> tuple[float, float, float, float] | None:
    today = dt.datetime.now(IST).date()
    for row in sorted(rows, key=lambda x: x[0]):
        stamp = dt.datetime.fromtimestamp(row[0], IST)
        if stamp.date() == today and stamp.hour == 9 and stamp.minute == 15:
            return row[1], row[2], row[3], row[4]
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