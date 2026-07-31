"""
CandleTracker — real-time 5-min OHLC builder from Angel One WebSocket ticks.

Replaces TradingView + Yahoo Finance as the primary data source for all
strategy screeners (Advance ORB, Big Players).

Architecture
────────────
  WebSocket on_data()  →  CandleTracker.on_tick()   →  strategy endpoints read
                           (updates live ticks +       get_candle_data() /
                            current 5-min OHLC)        get_200_ema()

Persistence
───────────
  Two files:
    candles.json        — Completed 5-min candles built from WebSocket ticks
                          (saved at every 5-min boundary + 60s safety net).
                          Size: small — only today's data, grows during
                          market hours.

    strategy_cache.json — Pre-computed 200 EMA per symbol + yesterday's
                          high. Populated lazily from yfinance on first
                          request; survives restarts so EMA is instant on
                          day 2+.  Size: ~20 KB — trivial to load.

  NOT saved on every tick — 727 stocks × ~250ms = hundreds of disk writes
  per second, which would stall tick processing.
"""

from __future__ import annotations

import json
import math
import threading
import time
from datetime import datetime, timezone, timedelta
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
EMA_LOOKBACK_DAYS = 4

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STOCKS_PATH = PROJECT_ROOT / "stocks" / "watchlist.json"
CANDLES_PATH = PROJECT_ROOT / "stocks" / "candles.json"
CACHE_PATH = PROJECT_ROOT / "stocks" / "strategy_cache.json"


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
    if dt.weekday() >= 5:
        return None
    return (cur - MARKET_OPEN_MIN) // SLOT_LENGTH


def _today_str() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


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
        # symbol → {open, high, low, close, volume, slot, date}
        self.current: dict[str, dict[str, Any]] = {}

        # ── completed (snapshotted) candles ──────────────────────────
        # completed[date_str][slot_idx][symbol] = {open, high, low, close, volume}
        self.completed: dict[str, dict[int, dict[str, dict[str, float]]]] = {}

        # ── slot tracking ────────────────────────────────────────────
        self._last_slot: int | None = None
        self._last_slot_date: str | None = None

        # ── persistence ──────────────────────────────────────────────
        self._lock = threading.Lock()
        self._last_save = time.time()
        self._save_interval = 60.0  # safety net for in-progress candle

        # ── strategy cache (loaded / saved separately) ───────────────
        # {symbol: {"ema": float|None, "yesterday_high": float|None}}
        self._cache: dict[str, dict] = {}

        self._load_stocks()
        self._load_candles()
        self._load_cache()

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

            # Yesterday high from cache
            cached = self._cache.get(symbol, {})
            yesterday_high = cached.get("yesterday_high")

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
        """200-period EMA on 5-min closes.

        Priority:
          1. Cache (loaded from strategy_cache.json or previous computation)
          2. Building from completed candles in memory
          3. yfinance fallback (lazy per-symbol, cached afterwards)
        """
        # 1. Check cache
        with self._lock:
            cached = self._cache.get(symbol)
            if cached is not None and "ema" in cached:
                ema = cached["ema"]
                if ema is not None and not math.isnan(ema):
                    return ema

        # 2. Build from completed candles
        closes = self._collect_closes(symbol)
        if len(closes) >= EMA_SPAN:
            ema = float(pd.Series(closes).ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])
            with self._lock:
                self._cache.setdefault(symbol, {})["ema"] = ema
                self._save_cache()
            return ema

        # 3. yfinance fallback
        ema = self._yfinance_ema(symbol)
        with self._lock:
            self._cache.setdefault(symbol, {})["ema"] = ema
            self._save_cache()
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
                return   # market closed

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
                if volume > 0:
                    c["volume"] = max(c.get("volume", 0), volume)

            # Safety-net save every 60s for in-progress candles
            if time.time() - self._last_save > self._save_interval:
                self._save_candles()

    # ── internals ────────────────────────────────────────────────────

    def _snapshot(self, slot: int, date_str: str) -> None:
        """Move all current candles for *slot* into ``completed`` and save."""
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
            # Reset for next slot
            c["open"] = c["close"]
            c["high"] = c["close"]
            c["low"] = c["close"]
            c["slot"] = slot + 1

        # Save at every 5-min boundary — primary persistence point
        self._save_candles()

    def _yesterday_high_from_yfinance(self, symbol: str) -> float | None:
        """Fetch yesterday's 5-min high from yfinance."""
        df = self._yfinance_5min(symbol)
        if df is None:
            return None
        idx = pd.DatetimeIndex(df.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC").tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)
        df.index = idx
        today = pd.Timestamp.now(tz=IST).date()
        # Find the most recent trading day before today
        all_dates = sorted(set(d for d in df.index.date if d < today), reverse=True)
        if not all_dates:
            return None
        prev_data = df[df.index.date == all_dates[0]]
        if prev_data.empty:
            return None
        return float(prev_data["High"].max())

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

    def _yfinance_5min(self, symbol: str) -> pd.DataFrame | None:
        """Download 4d of 5-min data; return single-level DataFrame or None."""
        ticker = f"{symbol.strip().upper()}.NS"
        try:
            df = yf.download(ticker, period=f"{EMA_LOOKBACK_DAYS}d", interval="5m",
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
            return df
        except Exception:
            return None

    def _yfinance_ema(self, symbol: str) -> float | None:
        """Download yfinance 5-min data & compute 200-period EMA."""
        df = self._yfinance_5min(symbol)
        if df is None:
            return None
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(closes) < EMA_SPAN:
            return None
        ema = closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
        return float(ema) if pd.notna(ema) else None

    def _yesterday_high(self, symbol: str) -> float | None:
        """Return yesterday's max high, using cache or yfinance fallback."""
        # Check cache first
        cached = self._cache.get(symbol, {})
        yh = cached.get("yesterday_high")
        if yh is not None and not math.isnan(yh):
            return yh

        # Try from completed candles
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
                    yh = max(highs)
                    with self._lock:
                        self._cache.setdefault(symbol, {})["yesterday_high"] = yh
                    return yh

        # Fall back: yfinance
        yh = self._yesterday_high_from_yfinance(symbol)
        if yh is not None:
            with self._lock:
                self._cache.setdefault(symbol, {})["yesterday_high"] = yh
                self._save_cache()
        return yh

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
        """Load saved candle data from candles.json."""
        if not CANDLES_PATH.exists():
            logger.info("📂 No candles.json found")
            return
        if CANDLES_PATH.stat().st_size > 50_000_000:  # >50MB = corrupted/too large
            logger.warning(f"⚠️ candles.json is {CANDLES_PATH.stat().st_size/1e6:.0f}MB — too large, ignoring")
            CANDLES_PATH.unlink(missing_ok=True)
            return
        try:
            with open(CANDLES_PATH) as f:
                data = json.load(f)
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

    def _save_candles(self) -> None:
        """Write completed candles to candles.json (compact format, today only)."""
        try:
            today_str = _today_str()
            # Only persist today's data (yesterday's is in strategy_cache.json)
            today_data = {
                k: v for k, v in self.completed.items()
                if k == today_str
            }
            if not today_data:
                return
            out: dict = {}
            for date_str, slots in today_data.items():
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

    def _load_cache(self) -> None:
        """Load strategy cache (EMA + yesterday_high) from strategy_cache.json."""
        if not CACHE_PATH.exists():
            logger.info("📂 No strategy_cache.json found — will build on demand")
            return
        try:
            with open(CACHE_PATH) as f:
                self._cache = json.load(f)
            logger.info(f"📂 Loaded strategy cache: {len(self._cache)} symbols")
        except Exception as e:
            logger.warning(f"⚠️ Failed to load cache: {e}")

    def _save_cache(self) -> None:
        """Write strategy cache to disk (tiny JSON, called after each new EMA)."""
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(CACHE_PATH, "w") as f:
                json.dump(self._cache, f, indent=2)
        except Exception as e:
            logger.error(f"🔴 Failed to save cache: {e}")


# ═════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═════════════════════════════════════════════════════════════════════

candle_tracker = CandleTracker()
"""Module-level singleton — import and use anywhere in the app."""
