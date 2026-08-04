"""
CandleTracker — real-time 5-min candle builder from Angel One WebSocket ticks.

Replaces TradingView + Yahoo Finance as the primary data source for all
strategy screeners (Advance ORB, Big Players).

─────────────── DATA FLOW ───────────────

  Angel One WebSocket ticks (~250ms)
          │
          ├──→ latest_ticks[token]        ← live price, yesterday close, day hi/lo
          │      (broker/angel_ws.py)
          │
          └──→ CandleTracker.on_tick()    ← builds 5-min OHLC in real-time
                 │
                 ├──→ live[symbol]         ← latest LTP, change%, volume
                 ├──→ current[symbol]      ← in-progress 5-min candle
                 └──→ completed[date][slot][symbol]  ← snapshotted at 5-min boundaries
                          │
                          └──→ get_candle_data(symbol)
                               returns: (small?, high915, open915, low915, close915,
                                         range%, day_low, yesterday_high, close920, inside_915)

  strategy_cache.json                     ← pre-computed from yfinance (background)
      {symbol: {ema, yesterday_high, yesterday_low, yesterday_close}}
      All 727 stocks populated at startup via background thread.
      Size: ~70 KB — loads instantly.

─────────────── PERSISTENCE ──────────────

  candles.json        — Today's completed 5-min candles from WebSocket.
                        Saved on every 5-min boundary + 60s safety net.
                        Size: small (only today, grows during market hours).

  strategy_cache.json — Pre-computed EMA + yesterday's stats for ALL 727 stocks.
                        Built once at startup via yfinance, survives restarts.
                        Size: ~70 KB — instant load.

─────────────── SOURCES PER FIELD ────────

  Field                      Source
  ────────────────────────────────────────────────────────
  Current LTP                WebSocket (latest_ticks)
  Yesterday's close          WebSocket (latest_ticks[token].close)
  Gap %                      Computed: (ltp - yest_close) / yest_close × 100
  Today's open               WebSocket (latest_ticks[token].open)
  Today's high/low           WebSocket (latest_ticks[token].high/.low)
  9:15 candle OHLC           CandleTracker from WebSocket ticks
  9:20 close + inside_915    CandleTracker from WebSocket ticks
  Day low so far             CandleTracker (min of today's completed slots)
  Yesterday's high/low/close strategy_cache.json (yfinance bootstrap)
  200 EMA                    strategy_cache.json (yfinance bootstrap)
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
MARKET_CLOSE_MIN = 15 * 60 + 45  # 15:45 IST
SLOT_LENGTH = 5                  # minutes per slot
EMA_SPAN = 200
EMA_LOOKBACK_DAYS = 4
CACHE_WORKERS = 8

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STOCKS_PATH = PROJECT_ROOT / "stocks" / "watchlist.json"
CANDLES_PATH = PROJECT_ROOT / "stocks" / "candles.json"
CACHE_PATH = PROJECT_ROOT / "stocks" / "strategy_cache.json"


# ─── helpers ─────────────────────────────────────────────────────────

def _slot_index(dt: datetime | None = None) -> int | None:
    """Current 5-min slot (0=09:15-09:20 … 74=15:25-15:30). None if market closed."""
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


def _yf_5min_df(symbol: str) -> pd.DataFrame | None:
    """Download 4 days of 5-min yfinance data for a symbol; return single-level DF or None."""
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
        #   symbol → {open, high, low, close, volume, slot, date}
        self.current: dict[str, dict[str, Any]] = {}

        # ── completed (snapshotted) candles ──────────────────────────
        #   completed[date_str][slot_idx][symbol] = {open, high, low, close, volume}
        self.completed: dict[str, dict[int, dict[str, dict[str, float]]]] = {}

        # ── slot tracking ────────────────────────────────────────────
        self._last_slot: int | None = None
        self._last_slot_date: str | None = None

        # ── persistence ──────────────────────────────────────────────
        self._lock = threading.Lock()
        self._last_save = time.time()
        self._save_interval = 60.0  # safety net save for in-progress candle

        # ── strategy cache (pre-computed from yfinance) ──────────────
        #   {symbol: {"ema": float, "yesterday_high": float,
        #              "yesterday_low": float, "yesterday_close": float}}
        self._cache: dict[str, dict] = {}
        self._cache_last_updated: str | None = None  # ISO timestamp from __meta__

        self._load_stocks()
        self._load_candles()
        self._load_cache()

        # ── bootstrap / daily refresh ────────────────────────────────
        if self._is_cache_stale():
            logger.info("⏳ Cache stale — starting background refresh for all 727 stocks…")
            t = threading.Thread(target=self._bootstrap_all, daemon=True)
            t.start()

        # ── daily refresh scheduler ──────────────────────────────────
        t = threading.Thread(target=self._daily_refresh_loop, daemon=True)
        t.start()

    # ═════════════════════════════════════════════════════════════════
    #  PUBLIC API
    # ═════════════════════════════════════════════════════════════════

    def get_candle_data(self, symbol: str) -> tuple:
        """Return today's 9:15 + 9:20 candle data and yesterday's stats.

        Returns (10-tuple, same as batch_opening_candle):
          (is_small, high915, open915, low915, close915, range_pct,
           day_low, yesterday_high, close920, inside_915)
        """
        with self._lock:
            today = self.completed.get(_today_str(), {})
            slot0 = today.get(0, {}).get(symbol, {})
            slot1 = today.get(1, {}).get(symbol, {})

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

            # Day low so far (from all completed slots today)
            all_lows = [
                c[symbol]["low"]
                for sidx, c in today.items()
                if symbol in c
            ]
            day_low = min(all_lows) if all_lows else None

            # Yesterday's data from cache
            cached = self._cache.get(symbol, {})
            yh = cached.get("yesterday_high")
            yl = cached.get("yesterday_low")
            yc = cached.get("yesterday_close")

            close920 = slot1.get("close") if slot1 else None
            inside_915 = bool(low <= close920 <= high) if close920 is not None else False

            return (is_small, high, opn, low, close915, rng_pct,
                    day_low, yh, close920, inside_915)

    def get_candle_data_batch(self, symbols: list[str]) -> dict:
        """Return candle data dict for a list of symbols."""
        return {s: self.get_candle_data(s) for s in symbols}

    def get_200_ema(self, symbol: str) -> float | None:
        """Return 200-period EMA from cache. Falls back to yfinance if missing."""
        cached = self._cache.get(symbol)
        if cached and "ema" in cached and cached["ema"] is not None:
            ema = cached["ema"]
            return ema if not math.isnan(ema) else None

        # Try building from completed candles
        closes = self._collect_closes(symbol)
        if len(closes) >= EMA_SPAN:
            ema = float(pd.Series(closes).ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])
            self._cache.setdefault(symbol, {})["ema"] = ema
            self._save_cache()
            return ema

        # yfinance fallback
        df = _yf_5min_df(symbol)
        if df is None:
            return None
        closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
        if len(closes) < EMA_SPAN:
            return None
        ema = float(closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])
        self._cache.setdefault(symbol, {})["ema"] = ema
        self._save_cache()
        return ema

    def get_200_ema_batch(self, symbols: list[str]) -> dict[str, float | None]:
        """Batch EMA — reads from cache (instant) or yfinance (per-symbol fallback)."""
        return {s: self.get_200_ema(s) for s in symbols}

    def get_yesterday_stats(self, symbol: str) -> dict:
        """Return yesterday's {high, low, close} from cache."""
        return self._cache.get(symbol, {})

    # ═════════════════════════════════════════════════════════════════
    #  WEBSOCKET HOOK
    # ═════════════════════════════════════════════════════════════════

    def on_tick(self, token: str, ltp: float, volume: int,
                open_price: float, high: float, low: float,
                close_price: float) -> None:
        """Called from angel_ws.on_data() on every tick."""
        symbol = self.symbol_by_token.get(token)
        if not symbol:
            return

        with self._lock:
            # Live snapshot
            chg_pct = ((ltp - close_price) / close_price * 100) if close_price > 0 else 0.0
            self.live[symbol] = {
                "ltp": ltp,
                "volume": volume,
                "change_pct": round(chg_pct, 2),
                "high": high,
                "low": low,
                "open": open_price,
                "close": close_price,   # ← yesterday's close (Angel One convention)
            }

            # ── 5-min candle update ──────────────────────────────────
            now = datetime.now(IST)
            slot = _slot_index(now)
            if slot is None:
                return  # market closed

            today_str = _today_str()

            # Slot transition → snapshot previous slot
            if (self._last_slot is not None
                    and self._last_slot_date == today_str
                    and slot != self._last_slot):
                self._snapshot(self._last_slot, today_str)

            self._last_slot = slot
            self._last_slot_date = today_str

            if symbol not in self.current:
                self.current[symbol] = {
                    "open": ltp, "high": ltp, "low": ltp, "close": ltp,
                    "volume": volume, "slot": slot, "date": today_str,
                }
            else:
                c = self.current[symbol]
                c["high"] = max(c["high"], ltp)
                c["low"] = min(c["low"], ltp)
                c["close"] = ltp
                if volume > 0:
                    c["volume"] = max(c.get("volume", 0), volume)

            # Safety-net save every 60s
            if time.time() - self._last_save > self._save_interval:
                self._save_candles()

    # ═════════════════════════════════════════════════════════════════
    #  INTERNALS
    # ═════════════════════════════════════════════════════════════════

    def _snapshot(self, slot: int, date_str: str) -> None:
        """Move all current candles for *slot* into ``completed`` and save."""
        if date_str not in self.completed:
            self.completed[date_str] = {}
        if slot not in self.completed[date_str]:
            self.completed[date_str][slot] = {}

        for sym, c in list(self.current.items()):
            if c.get("slot") == slot and c.get("date") == date_str:
                self.completed[date_str][slot][sym] = {
                    "open": c["open"], "high": c["high"],
                    "low": c["low"], "close": c["close"],
                    "volume": c["volume"],
                }
                # Reset for next slot
                c["open"] = c["close"]
                c["high"] = c["close"]
                c["low"] = c["close"]
                c["slot"] = slot + 1

        self._save_candles()  # save at every 5-min boundary

    def _collect_closes(self, symbol: str) -> list[float]:
        """All completed 5-min closes for a symbol across all dates in memory."""
        closes: list[float] = []
        for date_str in sorted(self.completed.keys()):
            for sidx in sorted(self.completed[date_str].keys()):
                c = self.completed[date_str][sidx].get(symbol)
                if c and c.get("close", 0) > 0:
                    closes.append(float(c["close"]))
        return closes

    # ═════════════════════════════════════════════════════════════════
    #  BACKGROUND BOOTSTRAP — ALL 727 STOCKS
    # ═════════════════════════════════════════════════════════════════

    def _bootstrap_all(self) -> None:
        """Background thread: compute EMA + yesterday's stats for ALL stocks from yfinance.

        Downloads 4 days of 5-min data per symbol, extracts just the 4 computed
        values (EMA, yesterday_high, yesterday_low, yesterday_close), and stores
        them in strategy_cache.json (~70 KB for 727 stocks).
        """
        symbols = list(self.token_by_symbol.keys())
        total = len(symbols)
        done = 0
        skipped = 0

        # Yesterday's date string — use last TRADING day in the data
        today = datetime.now(IST).date()

        def _get_last_trading_day(df) -> datetime.date | None:
            """Find the most recent complete day in *df* before today."""
            idx = pd.DatetimeIndex(df.index)
            if idx.tz is None:
                idx = idx.tz_localize("UTC").tz_convert(IST)
            else:
                idx = idx.tz_convert(IST)
            all_dates = sorted({d.date() for d in idx if d.date() < today})
            return all_dates[-1] if all_dates else None

        def _process(sym: str) -> tuple[str, dict | None]:
            df = _yf_5min_df(sym)
            if df is None:
                return (sym, None)

            idx = pd.DatetimeIndex(df.index)
            if idx.tz is None:
                idx = idx.tz_localize("UTC").tz_convert(IST)
            else:
                idx = idx.tz_convert(IST)
            df.index = idx

            closes = pd.to_numeric(df["Close"], errors="coerce").dropna()
            if len(closes) < EMA_SPAN:
                return (sym, None)

            ema = float(closes.ewm(span=EMA_SPAN, adjust=False).mean().iloc[-1])

            # Yesterday's data — use the last trading day, not calendar yesterday
            trade_date = _get_last_trading_day(df)
            yh = None
            yl = None
            yc = None
            if trade_date is not None:
                yesterday_data = df[df.index.date == trade_date]
                if not yesterday_data.empty:
                    yh = float(yesterday_data["High"].max())
                    yl = float(yesterday_data["Low"].min())
                    yc = float(yesterday_data["Close"].iloc[-1])

            result = {"ema": ema}
            if yh is not None:
                result["yesterday_high"] = yh
            if yl is not None:
                result["yesterday_low"] = yl
            if yc is not None:
                result["yesterday_close"] = yc
            return (sym, result)

        with ThreadPoolExecutor(max_workers=CACHE_WORKERS) as pool:
            futures = {pool.submit(_process, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    _, result = fut.result(timeout=60)
                    if result:
                        with self._lock:
                            self._cache[sym] = result
                        done += 1
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1

                if (done + skipped) % 100 == 0:
                    logger.info(f"📦 Bootstrap: {done} cached, {skipped} skipped ({done+skipped}/{total})")

        logger.info(f"✅ Bootstrap complete: {done} stocks cached, {skipped} skipped")
        if done > 0:
            self._save_cache()

    # ═════════════════════════════════════════════════════════════════
    #  PERSISTENCE
    # ═════════════════════════════════════════════════════════════════

    def _load_stocks(self) -> None:
        """Load 727-stock watchlist into memory."""
        if not STOCKS_PATH.exists():
            return
        with open(STOCKS_PATH) as f:
            data = json.load(f)
        for sym, info in data.get("symbols", {}).items():
            token = info["token"]
            self.symbol_by_token[token] = sym
            self.token_by_symbol[sym] = token
        logger.info(f"📋 Loaded {len(self.symbol_by_token)} stocks from watchlist.json")

    def _load_candles(self) -> None:
        """Load TODAY's completed candles from candles.json (if exists).

        Old-day data is purged on load — each day starts fresh.
        """
        if not CANDLES_PATH.exists():
            return
        if CANDLES_PATH.stat().st_size > 50_000_000:  # corrupted
            CANDLES_PATH.unlink(missing_ok=True)
            return
        try:
            today_str = _today_str()
            with open(CANDLES_PATH) as f:
                data = json.load(f)
            for date_str, slots_raw in data.get("candles", {}).items():
                if date_str != today_str:
                    continue  # purge old-day data
                self.completed[date_str] = {}
                for slot_str, symbols in slots_raw.items():
                    self.completed[date_str][int(slot_str)] = symbols
            total = sum(len(s) for d in self.completed.values() for s in d.values())
            logger.info(f"📂 Loaded candles: {len(self.completed)} days, {total} entries")
        except Exception as e:
            logger.warning(f"⚠️ candles.json load failed: {e}")

    def _save_candles(self) -> None:
        """Write today's completed candles to candles.json (old days purged).

        Old-day dates are dropped even when today has no data yet, so the
        file never retains stale prior-day candles.
        """
        today_str = _today_str()
        out = {}
        for date_str, slots in self.completed.items():
            if date_str != today_str:
                continue  # never persist old days
            out[date_str] = {str(s): syms for s, syms in slots.items()}
        payload = {"last_updated": datetime.now(IST).isoformat(), "candles": out}
        CANDLES_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(CANDLES_PATH, "w") as f:
            json.dump(payload, f, indent=2)
        self._last_save = time.time()

    def _is_cache_stale(self) -> bool:
        """Return True if cache needs refresh (empty, old format, or yesterday_data outdated)."""
        if not self._cache or len(self._cache) < 100:
            return True
        # Remove meta key for count check
        payload = {k: v for k, v in self._cache.items() if not k.startswith("__")}
        if len(payload) < 100:
            return True
        if not self._cache_last_updated:
            return True
        try:
            updated = datetime.fromisoformat(self._cache_last_updated)
            now = datetime.now(IST)
            # Stale on a new day — old data must be replaced with new-day data
            if updated.date() != now.date():
                return True
            # Stale if last update was before yesterday
            yesterday = (now - timedelta(days=1))
            if updated < yesterday:
                return True
        except Exception:
            return True
        return False

    def _daily_refresh_loop(self) -> None:
        """Background thread: refresh cache at ~9:00 AM IST each trading day."""
        while True:
            now = datetime.now(IST)
            # If it's between 8:30 AM and 9:15 AM on a weekday, refresh
            if now.weekday() < 5:
                mins = now.hour * 60 + now.minute
                if (8 * 60 + 30) <= mins <= (9 * 60 + 15):
                    if self._is_cache_stale():
                        logger.info("⏰ Daily refresh — cache is stale, re-bootstrapping…")
                        self._bootstrap_all()
            # Sleep 30 minutes between checks
            time.sleep(1800)

    def get_cache_status(self) -> dict:
        """Return cache status dict for the frontend indicator."""
        with self._lock:
            payload = {k: v for k, v in self._cache.items() if not k.startswith("__")}
            is_stale = self._is_cache_stale()
            return {
                "symbol_count": len(payload),
                "last_updated": self._cache_last_updated or None,
                "is_stale": is_stale,
                "status": "stale" if is_stale else "fresh",
            }

    def _load_cache(self) -> None:
        """Load pre-computed strategy cache from disk."""
        if not CACHE_PATH.exists():
            return
        try:
            with open(CACHE_PATH) as f:
                data = json.load(f)
            meta = data.pop("__meta__", {})
            self._cache_last_updated = meta.get("last_updated")
            self._cache = data
            logger.info(f"📂 Loaded strategy cache: {len(self._cache)} symbols"
                        f" (updated {self._cache_last_updated or 'unknown'})")
        except Exception as e:
            logger.warning(f"⚠️ Cache load failed: {e}")

    def _save_cache(self) -> None:
        """Write strategy cache to disk (tiny JSON)."""
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        now_ts = datetime.now(IST).isoformat()
        payload = dict(self._cache)
        payload["__meta__"] = {
            "last_updated": now_ts,
            "symbol_count": len([k for k in self._cache if not k.startswith("__")]),
        }
        # Also update in-memory so get_cache_status() reflects the fresh state
        self._cache_last_updated = now_ts
        with open(CACHE_PATH, "w") as f:
            json.dump(payload, f, indent=2)


# ═════════════════════════════════════════════════════════════════════
#  SINGLETON
# ═════════════════════════════════════════════════════════════════════

candle_tracker = CandleTracker()
"""Module-level singleton — import and use anywhere in the app."""
