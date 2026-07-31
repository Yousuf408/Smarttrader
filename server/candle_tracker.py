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
  candles.json is saved:
    a) On every 5-min boundary transition (when a candle is completed)
    b) Every 60s as a safety net for the current in-progress candle

  NOT on every tick — 727 stocks × ~250ms = hundreds of disk writes/second,
  which would stall tick processing. A JSON round-trip takes ~50ms for this
  dataset; doing it per-tick would drop 80%+ of frames.

Bootstrap
─────────
  On first startup (candles.json missing/empty), a background thread fetches
  4 trading days of 5-min yfinance data for all 727 symbols and pre-populates
  candles.json so the 200-period EMA works immediately.
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
from concurrent.futures import ThreadPoolExecutor, as_completed

from logzero import logger

# ─── constants ───────────────────────────────────────────────────────
IST = timezone(timedelta(hours=5, minutes=30))
MARKET_OPEN_MIN = 9 * 60 + 15   # 09:15 IST
MARKET_CLOSE_MIN = 15 * 60 + 30  # 15:30 IST
SLOT_LENGTH = 5                  # minutes per slot
EMA_SPAN = 200
BOOTSTRAP_DAYS = 4
BOOTSTRAP_WORKERS = 8

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


def _detect_trading_dates() -> list[str]:
    """Return the last BOOTSTRAP_DAYS trading dates (most recent first) by probing NIFTY."""
    try:
        probe = yf.download("^NSEI", period="7d", interval="5m",
                            progress=False, auto_adjust=False)
        if probe is None or probe.empty:
            return []
        if isinstance(probe.columns, pd.MultiIndex):
            probe = probe.xs("^NSEI", axis=1, level=-1)
        idx = pd.DatetimeIndex(probe.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC").tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)
        probe.index = idx
        # Collect dates with at least one 5-min candle after 09:15
        all_dates = sorted({
            d for d in set(probe.index.date)
            if len(probe[(probe.index.date == d) &
                         (probe.index.hour == 9) & (probe.index.minute >= 15)]) > 0
        }, reverse=True)
        return [d.strftime("%Y-%m-%d") for d in all_dates[:BOOTSTRAP_DAYS]]
    except Exception:
        return []


def _yfinance_5min(symbol: str) -> pd.DataFrame | None:
    """Download 4d of 5-min yfinance data for *symbol* and return a single-level DataFrame.

    Returns None on failure or empty data.
    """
    ticker = f"{symbol.strip().upper()}.NS"
    try:
        df = yf.download(ticker, period=f"{BOOTSTRAP_DAYS}d", interval="5m",
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
        self._save_interval = 60.0   # seconds — safety net for current candle

        # ── historical EMA data cache ────────────────────────────────
        self._ema_cache: dict[str, float | None] = {}  # symbol → precomputed EMA or None

        self._load_stocks()
        self._load_candles()

        # ── background 4-day yfinance bootstrap ──────────────────────
        if not self.completed:
            logger.info("⏳ Starting background 4-day yfinance bootstrap for all stocks…")
            t = threading.Thread(target=self._bootstrap_historical, daemon=True)
            t.start()

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

            yesterday_high = self._yesterday_high(symbol)

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

            # Safety-net periodic save (every 60s) for the current in-progress candle
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
            # Reset current entry so the next slot starts fresh
            c["open"] = c["close"]
            c["high"] = c["close"]
            c["low"] = c["close"]
            c["slot"] = slot + 1

        # ── Save to disk at every 5-min boundary ─────────────────────
        # This is the primary persistence point — each completed candle
        # is written immediately so crash recovery loses at most 5 min.
        self._save_candles()

    def _yesterday_high(self, symbol: str) -> float | None:
        """Return yesterday's max high for *symbol* from completed data."""
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
        df = _yfinance_5min(symbol)
        if df is None:
            return None
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(closes) < EMA_SPAN:
            return None
        ema = closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1]
        return float(ema) if pd.notna(ema) else None

    # ── background 4-day bootstrap ───────────────────────────────────

    def _bootstrap_historical(self) -> None:
        """Background thread: fetch 4 days of 5-min data for all stocks from yfinance.

        Populates ``self.completed`` with historical candles and saves to
        ``candles.json`` so the 200-period EMA and yesterday's high are
        immediately available on restart.
        """
        trading_dates = _detect_trading_dates()
        if not trading_dates:
            logger.warning("⚠️ Bootstrap: could not determine trading dates")
            return

        logger.info(f"📅 Bootstrap target dates: {trading_dates}")
        symbols = list(self.token_by_symbol.keys())
        total = len(symbols)
        loaded = 0

        def _fetch(symbol: str) -> tuple[str, dict[int, dict] | None]:
            df = _yfinance_5min(symbol)
            if df is None:
                return (symbol, None)

            idx = pd.DatetimeIndex(df.index)
            if idx.tz is None:
                idx = idx.tz_localize("UTC").tz_convert(IST)
            else:
                idx = idx.tz_convert(IST)
            df.index = idx

            result: dict[str, dict[int, dict]] = {}
            for td in trading_dates:
                td_date = datetime.strptime(td, "%Y-%m-%d").date()
                day_data = df[df.index.date == td_date]
                if day_data.empty:
                    continue
                slots: dict[int, dict] = {}
                for _, row in day_data.iterrows():
                    cur_mins = row.name.hour * 60 + row.name.minute
                    if cur_mins < MARKET_OPEN_MIN or cur_mins >= MARKET_CLOSE_MIN:
                        continue
                    slot = (cur_mins - MARKET_OPEN_MIN) // SLOT_LENGTH
                    if slot not in slots:
                        slots[slot] = {"open": 0.0, "high": 0.0, "low": float("inf"),
                                       "close": 0.0, "volume": 0}
                    c = slots[slot]
                    high = pd.to_numeric(row["High"], errors="coerce")
                    low = pd.to_numeric(row["Low"], errors="coerce")
                    close = pd.to_numeric(row["Close"], errors="coerce")
                    vol = pd.to_numeric(row["Volume"], errors="coerce")
                    if pd.notna(high):
                        c["high"] = max(c["high"], float(high))
                    if pd.notna(low):
                        c["low"] = min(c["low"], float(low))
                    if c["open"] == 0:
                        c["open"] = float(close) if pd.notna(close) else 0.0
                    if pd.notna(close):
                        c["close"] = float(close)
                    if pd.notna(vol):
                        c["volume"] = max(c.get("volume", 0), int(vol))
                    # Fix inf low
                    if c["low"] == float("inf"):
                        c["low"] = c["open"]

                if slots:
                    for s in slots:
                        if slots[s]["low"] == float("inf"):
                            slots[s]["low"] = slots[s]["open"]
                    result[td] = slots

            return (symbol, result if result else None)

        with ThreadPoolExecutor(max_workers=BOOTSTRAP_WORKERS) as pool:
            futures = {pool.submit(_fetch, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    symbol, data = fut.result(timeout=60)
                    if data:
                        with self._lock:
                            for date_str, slots in data.items():
                                if date_str not in self.completed:
                                    self.completed[date_str] = {}
                                for slot, entries in slots.items():
                                    if slot not in self.completed[date_str]:
                                        self.completed[date_str][slot] = {}
                                    self.completed[date_str][slot][symbol] = entries
                        loaded += 1
                except Exception:
                    pass

                if loaded > 0 and loaded % 100 == 0:
                    logger.info(f"📦 Bootstrap progress: {loaded}/{total} symbols loaded")

        logger.info(f"✅ Bootstrap complete: {loaded}/{total} symbols with historical data")
        if loaded > 0:
            self._save_candles()

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
            logger.info("📂 No saved candles found")
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
        """Write all completed candles to candles.json."""
        try:
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
