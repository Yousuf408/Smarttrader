"""
CandleTracker — real-time 5-min OHLC builder from Angel One WebSocket ticks.

Replaces TradingView + Yahoo Finance as the primary data source for all
strategy screeners (Advance ORB, Big Players).

Architecture
────────────
  WebSocket on_data()  →  CandleTracker.on_tick()   →  strategy endpoints read
                           (updates live ticks +       get_candle_data() /
                            current 5-min OHLC)        get_200_ema()

On restart: candles.json picks up where the last session left off.  If no
saved data exists, yfinance bootstraps historical 5-min closes for the
200-period EMA (happens lazily on first EMA request per symbol).
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import date, datetime, timezone, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf
import pandas as pd

from logzero import logger

# ─── constants ───────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN_MIN = 9 * 60 + 15   # 09:15 IST
MARKET_CLOSE_MIN = 15 * 60 + 30  # 15:30 IST
SLOT_LENGTH = 5                  # minutes per slot
EMA_SPAN = 200

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STOCKS_PATH = PROJECT_ROOT / "stocks" / "watchlist.json"
CANDLES_PATH = PROJECT_ROOT / "stocks" / "candles.json"


# ─── helpers ─────────────────────────────────────────────────────────

def _slot_index(dt: datetime | None = None) -> int | None:
    """Return the current 5-min slot index (0 = 09:15–09:20 … up to 74 = 15:25–15:30).

    Returns *None* when the market is closed.
    """
    if dt is None:
        dt = datetime.now(IST)
    cur = dt.hour * 60 + dt.minute
    if cur < MARKET_OPEN_MIN or cur >= MARKET_CLOSE_MIN:
        return None
    if dt.weekday() >= 5:          # Saturday / Sunday
        return None
    return (cur - MARKET_OPEN_MIN) // SLOT_LENGTH


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def _is_small_candle(high: float, low: float, threshold: float = 1.5) -> bool:
    """Return True if the candle range (as % of low) ≤ *threshold*."""
    if low <= 0:
        return False
    return ((high - low) / low) * 100 <= threshold


# ═════════════════════════════════════════════════════════════════════
#  CANDLE TRACKER
# ═════════════════════════════════════════════════════════════════════

class CandleTracker:
    """In-memory 5-min candle builder fed by WebSocket ticks."""

    def __init__(self) -> None:
        # ── stock index ──────────────────────────────────────────────
        self.symbol_by_token: dict[str, str] = {}     # token → symbol
        self.token_by_symbol: dict[str, str] = {}     # symbol → token

        # ── live ticks (latest snapshot per symbol) ──────────────────
        self.live: dict[str, dict[str, Any]] = {}

        # ── current (unfinished) 5-min candles ───────────────────────
        self.current: dict[str, dict[str, Any]] = {}  # symbol → {open, high, low, close, volume, slot, date}

        # ── completed (snapshotted) candles ──────────────────────────
        # structure:  completed[date_str][slot_idx][symbol] = {open, high, low, close, volume}
        self.completed: dict[str, dict[int, dict[str, dict[str, float]]]] = {}

        # ── slot tracking ────────────────────────────────────────────
        self._last_slot: int | None = None
        self._last_slot_date: str | None = None

        # ── persistence ──────────────────────────────────────────────
        self._lock = threading.Lock()
        self._last_save = time.time()
        self._save_interval = 30.0   # seconds
        self._bootstrap_attempted = False

        # ── historical EMA data cache ────────────────────────────────
        # {symbol: [close, close, …]}  – ordered list of 5-min closes
        self._ema_closes: dict[str, list[float]] = {}
        self._ema_cache: dict[str, float | None] = {}  # symbol → precomputed EMA or None

        self._load_stocks()
        self._load_candles()

    # ── public API used by strategy screeners ────────────────────────

    def get_candle_data(self, symbol: str) -> tuple:
        """Return the same 10-tuple as the legacy ``batch_opening_candle``.

        Returns
        -------
        (is_small, high915, open915, low915, close915, range_pct,
         day_low, yesterday_high, close920, inside_915)
        """
        with self._lock:
            today = self.completed.get(_today_str(), {})
            slot0 = {}
            slot1 = {}
            if 0 in today:
                slot0 = today[0].get(symbol, {})
            if 1 in today:
                slot1 = today[1].get(symbol, {})

            if not slot0:
                return (False, None, None, None, None, None, None, None, None, None)

            high = slot0["high"]
            low = slot0["low"]
            opn = slot0["open"]
            close915 = slot0["close"]

            if low <= 0:
                return (False, None, None, None, None, None, None, None, None, None)

            rng_pct = ((high - low) / low) * 100
            is_small = rng_pct <= 1.5

            # Day low: scan *all* completed slots for today
            all_today_lows = [
                c[symbol]["low"]
                for sidx, c in today.items()
                if symbol in c
            ]
            day_low = min(all_today_lows) if all_today_lows else None

            yesterday_high = self._yesterday_high(symbol, today)

            close920 = slot1.get("close") if slot1 else None
            inside_915 = bool(low <= close920 <= high) if close920 is not None else False

            return (is_small, high, opn, low, close915, rng_pct,
                    day_low, yesterday_high, close920, inside_915)

    def get_candle_data_batch(self, symbols: list[str]) -> dict:
        """Like ``batch_opening_candle`` — CandleTracker edition.

        Returns ``{symbol: 10-tuple}``, missing symbols get the all-None tuple.
        """
        out = {}
        for sym in symbols:
            out[sym] = self.get_candle_data(sym)
        return out

    def get_200_ema(self, symbol: str) -> float | None:
        """200-period EMA on 5-min closes.  Falls back to yfinance if needed."""
        # Check cache
        with self._lock:
            cached = self._ema_cache.get(symbol)
            if cached is not None:
                return cached if not math.isnan(cached) else None

        # Try building from completed candles first
        closes = self._collect_closes(symbol)
        if len(closes) >= EMA_SPAN:
            ema = pd.Series(closes).ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
            with self._lock:
                self._ema_cache[symbol] = ema
            return float(ema)

        # Fall back: yfinance bootstrap
        ema = self._yfinance_ema_fallback(symbol)
        with self._lock:
            self._ema_cache[symbol] = ema
        return ema

    def get_200_ema_batch(self, symbols: list[str]) -> dict[str, float | None]:
        """Batch version — same interface as ``compute_200_ema_batch``."""
        return {s: self.get_200_ema(s) for s in symbols}

    # ── called from WebSocket on_data ────────────────────────────────

    def on_tick(self, token: str, ltp: float, volume: int,
                open_price: float, high: float, low: float,
                close_price: float) -> None:
        """Process one tick.  Thread-safe (lock around the mutable state)."""
        symbol = self.symbol_by_token.get(token)
        if not symbol:
            return

        with self._lock:
            # ── live snapshot ────────────────────────────────────────
            chg_pct = ((ltp - close_price) / close_price * 100) if close_price > 0 else 0.0
            self.live[symbol] = {
                "ltp": ltp,
                "volume": volume,
                "change_pct": round(chg_pct, 2),
                "high": high,
                "low": low,
                "open": open_price,
                "close": close_price,
            }

            # ── 5-min candle update ──────────────────────────────────
            now = datetime.now(IST)
            slot = _slot_index(now)
            if slot is None:
                return   # market closed — nothing to track

            today_str = _today_str()

            # Detect slot transition → snapshot previous slot
            if (self._last_slot is not None
                    and self._last_slot_date == today_str
                    and slot != self._last_slot):
                self._snapshot(self._last_slot, today_str)

            self._last_slot = slot
            self._last_slot_date = today_str

            if symbol not in self.current:
                self.current[symbol] = {
                    "open": ltp,
                    "high": ltp,
                    "low": ltp,
                    "close": ltp,
                    "volume": volume,
                    "slot": slot,
                    "date": today_str,
                }
            else:
                c = self.current[symbol]
                c["high"] = max(c["high"], ltp)
                c["low"] = min(c["low"], ltp)
                c["close"] = ltp
                # Take the cumulative volume (Angel One sends day-volume, not per-tick)
                if volume > 0:
                    c["volume"] = max(c.get("volume", 0), volume)

            # Periodic auto-save
            if time.time() - self._last_save > self._save_interval:
                self._flush_candles()

    # ── internals ────────────────────────────────────────────────────

    def _snapshot(self, slot: int, date_str: str) -> None:
        """Move all current candles for *slot* into ``completed``."""
        if date_str not in self.completed:
            self.completed[date_str] = {}
        if slot not in self.completed[date_str]:
            self.completed[date_str][slot] = {}

        to_snapshot = [
            (sym, c) for sym, c in self.current.items()
            if c.get("slot") == slot and c.get("date") == date_str
        ]
        for sym, c in to_snapshot:
            self.completed[date_str][slot][sym] = {
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }
            # Reset current entry so the next slot starts fresh
            # (keep it alive, just update slot)
            c["open"] = c["close"]   # next candle opens at last tick's price
            c["high"] = c["close"]
            c["low"] = c["close"]
            c["slot"] = slot + 1

    def _yesterday_high(self, symbol: str, today_candles: dict) -> float | None:
        """Return yesterday's max high for *symbol* from completed data."""
        # Check yesterday in completed
        all_dates = sorted(self.completed.keys(), reverse=True)
        today_str = _today_str()
        for d in all_dates:
            if d < today_str:
                slots = self.completed[d]
                highs = [
                    c[symbol]["high"]
                    for sidx, c in slots.items()
                    if symbol in c
                ]
                if highs:
                    return max(highs)
        return None

    def _collect_closes(self, symbol: str) -> list[float]:
        """Collect all completed 5-min closes for *symbol* across all dates."""
        closes: list[float] = []
        for date_str in sorted(self.completed.keys()):
            slots = self.completed[date_str]
            for sidx in sorted(slots.keys()):
                c = slots[sidx].get(symbol)
                if c and c.get("close", 0) > 0:
                    closes.append(float(c["close"]))
        return closes

    def _yfinance_ema_fallback(self, symbol: str) -> float | None:
        """Fetch 5-min data from yfinance & compute 200 EMA."""
        ticker = f"{symbol.strip().upper()}.NS"
        try:
            df = yf.download(ticker, period="4d", interval="5m",
                             progress=False, auto_adjust=False)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                try:
                    df = df.xs(ticker, axis=1, level=-1)
                except (KeyError, IndexError):
                    try:
                        df = df.xs(ticker, axis=1, level=0)
                    except (KeyError, IndexError):
                        return None
            closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(closes) < EMA_SPAN:
                return None
            ema = closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
            return float(ema) if pd.notna(ema) else None
        except Exception:
            return None

    # ── persistence ──────────────────────────────────────────────────

    def _load_stocks(self) -> None:
        """Load symbol→token mapping from watchlist.json."""
        if not STOCKS_PATH.exists():
            logger.warning(f"⚠️ {STOCKS_PATH} not found")
            return
        try:
            with open(STOCKS_PATH) as f:
                data = json.load(f)
            for sym, info in data.get("symbols", {}).items():
                token = info["token"]
                self.symbol_by_token[token] = sym
                self.token_by_symbol[sym] = token
            logger.info(f"📋 Loaded {len(self.symbol_by_token)} stocks from watchlist.json")
        except Exception as e:
            logger.error(f"🔴 Failed to load stocks: {e}")

    def _load_candles(self) -> None:
        """Load saved candles from candles.json (if any)."""
        if not CANDLES_PATH.exists():
            logger.info("📂 No saved candles found — will bootstrap from yfinance on demand")
            return
        try:
            with open(CANDLES_PATH) as f:
                data = json.load(f)
            # Convert int keys back (JSON serialises them as strings)
            raw = data.get("candles", {})
            for date_str, slots_raw in raw.items():
                self.completed[date_str] = {}
                for slot_str, symbols in slots_raw.items():
                    slot = int(slot_str)
                    self.completed[date_str][slot] = symbols
            total = sum(
                len(symbols)
                for d in self.completed.values()
                for slot, symbols in d.items()
            )
            logger.info(f"📂 Loaded candle data: {len(self.completed)} days, {total} symbol-slots")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load candles.json: {e}")

    def _flush_candles(self) -> None:
        """Write current completed candles to candles.json."""
        try:
            # Serialise: slot int → str for JSON compat
            out: dict = {}
            for date_str, slots in self.completed.items():
                out[date_str] = {}
                for slot, symbols in slots.items():
                    out[date_str][str(slot)] = symbols

            payload = {
                "last_updated": datetime.now(IST).isoformat(),
                "candles": out,
            }
            CANDLES_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CANDLES_PATH, "w") as f:
                json.dump(payload, f, indent=2)
            self._last_save = time.time()
        except Exception as e:
            logger.error(f"🔴 Failed to save candles: {e}")


# ═════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═════════════════════════════════════════════════════════════════════

candle_tracker = CandleTracker()
"""Module-level singleton — import and use anywhere in the app."""
