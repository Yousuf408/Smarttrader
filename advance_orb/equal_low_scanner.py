"""Equal-Low intraday scanner for NSE stocks (Yahoo Finance).

Scanning rules implemented here:

Rule 1 — 09:15 High-Break invalidation:
    The session's first 5-min candle (09:15 IST) defines a reference High.
    If ANY subsequent candle's High exceeds that reference, the stock is
    *invalidated for the rest of the session* and added to a blacklist so
    later polling loops skip it cheaply (no repeated Yahoo calls).

Rule 2 — Equal-Low detection:
    If the 09:15 High was NOT broken, we compare the latest candle's Low
    against the previous candles' Lows within a *lookback* window (going
    back to, but not past, today's 09:15 candle).
        diff_pct = abs(current_low - prev_low) / prev_low * 100
    If diff_pct <= tolerance_pct (default 0.08%), an Equal-Low match fires.

Persistence / memory optimisation:
    * `EqualLowSession.blacklisted` — stocks whose 09:15 High was broken
      are skipped by later scans in the same session.
    * `EqualLowSession.fixed` — once a stock matches an Equal Low it is
      "pinned" and never re-scanned; crucially a *pinned* match survives
      even if the stock later breaks its 09:15 High (it is not removed).

Everything is threaded-safe so the same session object can be shared by
the FastAPI screener and/or a standalone cron-style polling loop.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

logger = logging.getLogger("advance_orb.equal_low")

IST = ZoneInfo("Asia/Kolkata")

# ── tuning defaults (from the user's spec) ─────────────────────────
DEFAULT_TOLERANCE_PCT = 0.08   # equal-low tolerance, per cent
DEFAULT_LOOKBACK = 5           # how many prior candles to compare against
_UNSET = object()


# ────────────────────────────────────────────────────────────────────
# Data helpers
# ────────────────────────────────────────────────────────────────────
def append_ns(symbol: str) -> str:
    """Return *symbol* with the NSE '.NS' suffix, when not already present."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return ""
    return sym if sym.endswith(".NS") else f"{sym}.NS"


def fetch_5m_candles(
    symbol: str,
    period: str = "1d",
    interval: str = "5m",
    max_retries: int = 2,
) -> pd.DataFrame | None:
    """Download and normalise today's 5-minute candles for an NSE stock.

    Returns a DataFrame indexed by IST Datetime with lowercase Open/High/
    Low/Close/Volume columns (the ``Datetime`` column is the index), or
    ``None`` when Yahoo returned nothing usable (rate-limited / delisted /
    market-closed with no bars).
    """
    ticker = append_ns(symbol)
    if not ticker:
        return None

    last_err: Exception | None = None
    for attempt in range(max(1, max_retries)):
        try:
            df = yf.download(
                tickers=ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=False,
                prepost=False,
                threads=False,
            )
        except Exception as exc:  # noqa: BLE001 - yfinance raises broadly
            last_err = exc
            time.sleep(min(1, 0.5 * (attempt + 1)))
            continue

        if df is None or df.empty:
            return None

        # Collapse multi-column frames (when downloading one symbol Yahoo
        # may still wrap columns in a MultiIndex).
        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(ticker, axis=1, level=-1)
            except (KeyError, IndexError):
                try:
                    df = df.xs(ticker, axis=1, level=0)
                except (KeyError, IndexError):
                    return None

        required = {"Open", "High", "Low", "Close"}
        if not required.issubset(df.columns):
            return None

        idx = pd.DatetimeIndex(df.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC").tz_convert(IST)
        else:
            idx = idx.tz_convert(IST)

        out = pd.DataFrame({
            "open": pd.to_numeric(df["Open"], errors="coerce"),
            "high": pd.to_numeric(df["High"], errors="coerce"),
            "low": pd.to_numeric(df["Low"], errors="coerce"),
            "close": pd.to_numeric(df["Close"], errors="coerce"),
            "volume": pd.to_numeric(df["Volume"], errors="coerce").fillna(0),
        }).dropna(subset=["high", "low", "close"])
        out.index = idx
        out = out.sort_index()
        if len(out) >= 1:
            return out

        return None

    if last_err is not None:
        logger.warning("equal_low: yfinance failed for %s: %s", ticker, last_err)
    return None


def anchor_rows(df: pd.DataFrame, on: dt.date | None = None) -> pd.DataFrame:
    """Return only rows for the given IST date (today when ``on`` is None)."""
    target = on or dt.datetime.now(IST).date()
    rows = df[df.index.date == target]
    return rows


# ────────────────────────────────────────────────────────────────────
# Rule 1 — 09:15 High-Break
# ────────────────────────────────────────────────────────────────────
def first_candle(df: pd.DataFrame) -> tuple[float, float, float]:
    """Return (high, low, open) of the 09:15 IST candle.

    When no explicit 09:15 bar exists (bad feed), falls back to the first
    row of the frame so the caller can still make a defensive decision.
    """
    if df is None or df.empty:
        return (0.0, 0.0, 0.0)

    nine15 = df[(df.index.hour == 9) & (df.index.minute >= 15)]
    if not nine15.empty:
        row = nine15.iloc[0]
    else:
        row = df.iloc[0]

    high = float(row["high"] or 0.0)
    low = float(row["low"] or 0.0)
    opn = float(row["open"] or 0.0)
    return high, low, opn


def high_broken(df: pd.DataFrame, first_high: float) -> bool:
    """Rule 1 — True when any candle after 09:15 broke the first candle high."""
    if df is None or df.empty or first_high <= 0:
        return False
    after = df.iloc[1:]  # strictly after the first (09:15) bar
    if after.empty:
        return False
    return bool(float(after["high"].max()) > first_high)


# ────────────────────────────────────────────────────────────────────
# Rule 2 — Equal-Low detection
# ────────────────────────────────────────────────────────────────────
def detect_equal_low(
    df: pd.DataFrame,
    first_high: float,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    lookback: int = DEFAULT_LOOKBACK,
) -> dict[str, Any] | None:
    """Rule 2 — compare the latest candle's Low to recent Lows.

    Returns a match dict ``{low, matched_low, diff_pct, sample_count}`` when
    a recent price repeated near the same low, else ``None``.
    """
    if df is None or df.empty or len(df) < 2:
        return None

    current = df.iloc[-1]
    current_low = float(current["low"] or 0.0)
    if current_low <= 0:
        return None

    _ts = lambda pos: df.index[pos].strftime("%H:%M")  # noqa: E731

    limit = min(len(df), max(2, int(lookback) + 2))  # +1 for current, +1 extra
    for i in range(2, limit):  # 2 .. lookback+1 -> skips the current row
        prev_low = float(df["low"].iloc[-i] or 0.0)
        if prev_low <= 0:
            continue
        diff_pct = abs(current_low - prev_low) / prev_low * 100.0
        if diff_pct <= tolerance_pct:
            return {
                "current_time": _ts(-1),
                "low": round(current_low, 2),
                "matched_time": _ts(-i),
                "matched_low": round(prev_low, 2),
                "diff_pct": round(diff_pct, 4),
                "sample_count": i,
            }
    return None


# ────────────────────────────────────────────────────────────────────
# Session state (blacklist + pinned matches) — the in-RAM optimization
# ────────────────────────────────────────────────────────────────────
class EqualLowSession:
    """Thread-safe per-session state for the scanner.

    Attributes
    ----------
    blacklisted : set[str]
        Symbols whose 09:15 High was broken — never scanned again.
    fixed : dict[str, dict[str, Any]]
        Symbols that already matched an Equal Low, pinned for the session.
        A pinned match survives a later 09:15 High break.
    date : dt.date
        IST date this session is scoped to. Polls on a different day start
        fresh state automatically.
    """

    def __init__(self, date: dt.date | None = None):
        self.date = date or dt.datetime.now(IST).date()
        self._lock = threading.Lock()
        self.blacklisted: set[str] = set()
        self.fixed: dict[str, dict[str, Any]] = {}

    def _ensure_day(self, on: dt.date | None = None) -> bool:
        """Reset state when the trading day changed (auto day-rollover)."""
        today = on or dt.datetime.now(IST).date()
        if today != self.date:
            self.date = today
            self.blacklisted.clear()
            self.fixed.clear()
            return True
        return False

    def needs_scan(self, symbol: str, on: dt.date | None = None) -> bool:
        """True when this symbol still needs work this session.

        A symbol is skipped when it is blacklisted (High broken) or already
        pinned (Equal-Low matched). In both cases no Yahoo call is made.
        """
        self._ensure_day(on)
        sym = append_ns(symbol).replace(".NS", "")
        with self._lock:
            if sym in self.blacklisted or sym in self.fixed:
                return False
            return True

    def mark_broken(self, symbol: str) -> None:
        """Rule 1 — blacklist a symbol whose 09:15 High was broken."""
        self._ensure_day()
        sym = append_ns(symbol).replace(".NS", "")
        with self._lock:
            # A pinned (already equal-low matched) symbol is NOT blacklisted
            # so the pinned match stays visible. See `fixed` doc note.
            if sym not in self.fixed:
                self.blacklisted.add(sym)

    def pin_match(self, symbol: str, match: dict[str, Any]) -> None:
        """Rule 2 — pin a symbol once it shares an Equal Low."""
        self._ensure_day()
        sym = append_ns(symbol).replace(".NS", "")
        with self._lock:
            self.fixed[sym] = dict(match)
            self.blacklisted.discard(sym)


def scan_symbol(
    symbol: str,
    session: EqualLowSession | None = None,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    lookback: int = DEFAULT_LOOKBACK,
    # Sentinel so callers can pass fetch=None and still defer to yfinance.
    fetch=_UNSET,
) -> dict[str, Any]:
    """Run the full workflow for one symbol against a session.

    Returns a dict with a stable ``code`` so callers can switch:
        PICKLED       already pinned (equal-low matched earlier)
        BLACKLISTED   already invalidated (09:15 high broken earlier)
        MATCHED       fresh equal-low match (now pinned)
        HIGH_BROKEN   09:15 high broken -> blacklisted
        NO_MATCH      scanned, no equal-low
        INSUFFICIENT  too few/no bars
        FETCH_ERROR   Yahoo failed
    """
    session = session or EqualLowSession()
    sym = append_ns(symbol).replace(".NS", "")
    if not sym:
        return {"code": "FETCH_ERROR", "symbol": symbol, "error": "empty symbol"}

    session._ensure_day()
    with session._lock:
        if sym in session.fixed:
            return {"code": "PINNED", "symbol": sym, "match": dict(session.fixed[sym])}
        if sym in session.blacklisted:
            return {"code": "BLACKLISTED", "symbol": sym}

    if fetch is _UNSET:
        df = fetch_5m_candles(sym)
    else:
        df = fetch(sym)
    if df is None or df.empty:
        return {"code": "INSUFFICIENT", "symbol": sym}

    first_high, first_low, first_open = first_candle(df)

    # Rule 1
    if high_broken(df, first_high):
        session.mark_broken(sym)
        return {"code": "HIGH_BROKEN", "symbol": sym,
                "first_high": round(first_high, 2)}

    # Rule 2
    match = detect_equal_low(df, first_high, tolerance_pct, lookback)
    if match is not None:
        session.pin_match(sym, match)
        return {"code": "MATCHED", "symbol": sym, "match": match,
                "first_high": round(first_high, 2)}

    return {"code": "NO_MATCH", "symbol": sym,
            "first_high": round(first_high, 2)}


def scan_batch(
    symbols: list[str],
    session: EqualLowSession | None = None,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    lookback: int = DEFAULT_LOOKBACK,
    workers: int = 5,
) -> dict[str, dict[str, Any]]:
    """Concurrently scan many symbols; blacklisted/pinned ones are skipped.

    Returns ``{symbol: result_dict}`` — ideal for a polling loop that runs
    every N minutes during the session: broken stocks stop being fetched
    (API-call savings) while pinned equal-low stocks stay fixed.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    session = session or EqualLowSession()
    to_scan = [s for s in symbols if session.needs_scan(s)]
    results: dict[str, dict[str, Any]] = {}
    if not to_scan:
        return results

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(scan_symbol, s, session, tolerance_pct, lookback): s
                   for s in to_scan}
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                results[s] = fut.result()
            except Exception as exc:  # defensive: never kill the loop
                logger.error("equal_low: scan crashed for %s: %s", s, exc)
                results[s] = {"code": "FETCH_ERROR", "symbol": s, "error": str(exc)}
    return results


# ────────────────────────────────────────────────────────────────────
# Inside-9:15 gated equal-low (used by the Advance ORB "Share Low" column)
# ────────────────────────────────────────────────────────────────────
def _time_hm(value: str) -> float:
    """Parse 'HH:MM' to minutes since midnight (for cutoff comparisons)."""
    try:
        hh, mm = value.split(":")
        return int(hh) * 60 + int(mm)
    except (ValueError, AttributeError):
        return 0.0


def equal_low_inside_915(
    candles: list[dict[str, Any]],
    high915: float | None,
    low915: float | None,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
    lookback: int = DEFAULT_LOOKBACK,
    cutoff: str = "11:00",
) -> dict[str, Any] | None:
    """Equal-Low using REAL candle timestamps, gated to the 9:15 range.

    ``candles`` is oldest→newest, each ``{"t": "HH:MM", "high": float,
    "low": float}`` (today's bars only).

    Rules enforced here:
      * Only candles up to ``cutoff`` (default 11:00) are tracked — lows
        after 11:00 AM are ignored.
      * A match counts ONLY when BOTH the current candle AND the matched
        previous candle sit inside the 09:15 opening range — i.e.
        ``low >= low915 and high <= high915``.
      * The whole 09:15→cutoff window is scanned (not just the very last
        candle), so a stock that tagged the same low earlier in the morning
        is still caught even if it later breaks out of the range.  The most
        recent valid pair is reported.

    Returns ``{"current_time", "low", "matched_time", "matched_low",
    "diff_pct"}`` using real candle timestamps (never fabricated), or None.
    """
    if not candles or len(candles) < 2:
        return None
    if high915 is None or low915 is None or high915 <= 0:
        return None

    limit = _time_hm(cutoff)

    def inside(c: dict[str, Any]) -> bool:
        hi = float(c.get("high") or 0.0)
        lo = float(c.get("low") or 0.0)
        return bool(hi > 0 and lo > 0 and lo >= low915 and hi <= high915)

    best: dict[str, Any] | None = None
    for i in range(1, len(candles)):
        cur = candles[i]
        if _time_hm(str(cur.get("t", ""))) > limit:
            break  # candles are chronological -> stop at the cutoff
        cur_low = float(cur.get("low") or 0.0)
        if cur_low <= 0 or not inside(cur):
            continue

        start = i - 1
        end = max(-1, i - 1 - lookback)
        for j in range(start, end, -1):
            prev = candles[j]
            prev_low = float(prev.get("low") or 0.0)
            if prev_low <= 0 or not inside(prev):
                continue
            diff_pct = abs(cur_low - prev_low) / prev_low * 100.0
            if diff_pct <= tolerance_pct:
                # Most recent pair wins; overwrite so best ends the latest match.
                best = {
                    "current_time": cur["t"],
                    "low": round(cur_low, 2),
                    "matched_time": prev["t"],
                    "matched_low": round(prev_low, 2),
                    "diff_pct": round(diff_pct, 4),
                }
                break

    return best


# ────────────────────────────────────────────────────────────────────
# Forward-looking helper for the screener (cheap, no extra Yahoo calls)
# ────────────────────────────────────────────────────────────────────
def equal_low_from_lows(
    lows: list[float | None],
    labels: list[str] | None = None,
    tolerance_pct: float = DEFAULT_TOLERANCE_PCT,
) -> dict[str, Any] | None:
    """Derive an Equal-Low match from bare candle Lows (oldest→newest).

    Used by the Advance ORB backend to render the "Share Low" column from
    the lows the screener already fetched (low915 + successive candle lows)
    without issuing any additional Yahoo calls per symbol.

    ``labels`` (optional) are the per-candle time labels in the same order
    as ``lows``; when provided the returned match includes ``current_time``
    and ``matched_time`` so the UI can say *"9:40 candle sharing low with
    9:25"* instead of showing only the price level.
    """
    valid = [float(l) for l in lows if l is not None and float(l) > 0]
    if len(valid) < 2:
        return None
    # Positions (in the ORIGINAL list) of the valid lows, oldest→newest.
    pos = [i for i, l in enumerate(lows) if l is not None and float(l) > 0]
    cur_label = None
    if labels:
        try:
            cur_label = labels[pos[-1]]
        except IndexError:
            cur_label = None

    current_low = valid[-1]
    # The prev candle we hit FIRST is valid[-2] = pos[-2] in the original list.
    matched_label = None
    if labels and len(pos) >= 2:
        try:
            matched_label = labels[pos[-2]]
        except IndexError:
            matched_label = None

    for prev in valid[-2::-1]:  # most recent first, skipping the current low
        diff_pct = abs(current_low - prev) / prev * 100.0
        if diff_pct <= tolerance_pct:
            result: dict[str, Any] = {
                "low": round(current_low, 2),
                "matched_low": round(prev, 2),
                "diff_pct": round(diff_pct, 4),
            }
            if labels and cur_label is not None:
                result["current_time"] = cur_label
                result["matched_time"] = matched_label
            return result
    return None


# ────────────────────────────────────────────────────────────────────
# Standalone CLI: scan a watchlist once
# ────────────────────────────────────────────────────────────────────
def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="NSE Equal-Low intraday scanner")
    parser.add_argument("symbols", nargs="+", help="NSE tickers ('.NS' auto-appended)")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE_PCT,
                        help="equal-low tolerance %% (default 0.08)")
    parser.add_argument("--lookback", type=int, default=DEFAULT_LOOKBACK,
                        help="lookback candles (default 5)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)

    session = EqualLowSession()
    results = scan_batch(args.symbols, session,
                         tolerance_pct=args.tolerance, lookback=args.lookback)
    if args.json:
        print(json.dumps(results, indent=2, default=str))
        return 0

    for symbol in args.symbols:
        r = results.get(symbol, {"code": "FETCH_ERROR"})
        code = r.get("code")
        if code in ("MATCHED", "PINNED"):
            m = r.get("match", {})
            print(f"🎯 {r['symbol']} EQUAL-LOW @ {m.get('low')} "
                  f"(match {m.get('matched_low')}, {m.get('diff_pct', 0)}%)")
        elif code == "HIGH_BROKEN":
            print(f"🚫 {r['symbol']} broke 09:15 High ({r.get('first_high')}) — blacklisted")
        elif code == "BLACKLISTED":
            print(f"⏭️  {r['symbol']} blacklisted earlier — skipped")
        elif code == "INSUFFICIENT":
            print(f"ℹ️  {r['symbol']} — insufficient data")
        elif code == "FETCH_ERROR":
            print(f"❌ {r['symbol']} — fetch error: {r.get('error', 'unknown')}")
        else:
            print(f"➖ {r['symbol']} — no equal-low match")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
