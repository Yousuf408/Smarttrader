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
from typing import Any

import requests
import websockets

logger = logging.getLogger("advance_orb.tv_chart")
IST = dt.timezone(dt.timedelta(hours=5, minutes=30))


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


async def _batch_tv_opening_candles_async(
    result: dict[str, tuple[float, float, float, float] | None],
    token: str,
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
            await ws.send(_frame("create_series", [chart, series, series, alias, "5", 500]))
            raw = ""
            while True:
                message = await ws.recv()
                raw += message
                if "series_completed" in message:
                    break
            candle = _first_candle(_series_rows(raw))
            if candle:
                result[symbol] = candle


def batch_tv_opening_candles(symbols: list[str]) -> dict[str, tuple[float, float, float, float] | None]:
    """Fetch today's exact 09:15–09:20 bar for symbols in one TV session."""
    result = {str(s).strip().upper(): None for s in symbols if s}
    # Before the first candle closes there is nothing valid to fetch.
    now = dt.datetime.now(IST)
    if (now.hour, now.minute) < (9, 20):
        return result
    username = os.getenv("TRADINGVIEW_USERNAME", "").strip()
    password = os.getenv("TRADINGVIEW_PASSWORD", "")
    if not username or not password or not result:
        return result

    try:
        login = requests.post(
            "https://www.tradingview.com/accounts/signin/",
            data={"username": username, "password": password, "remember": "on"},
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.tradingview.com"},
            timeout=20,
        ).json()
        token = (login.get("user") or {}).get("auth_token", "")
        if not token:
            logger.warning("TradingView login did not return auth_token: %s", login.get("error", "unknown error"))
            return result

        asyncio.run(_batch_tv_opening_candles_async(result, token))
    except Exception as exc:
        logger.warning("TradingView 5-minute candle fetch failed: %s", exc)
    return result