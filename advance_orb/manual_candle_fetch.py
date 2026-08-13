"""Manual TradingView candle fetch (tvdatafeed → existing ORB JSON store).

On-demand backfill driver.  Clicking the "Get Data" button starts a background
job that pulls the FULL 5-min and 15-min candle history for the whole ORB
universe straight from TradingView (via the ``tvdatafeed`` library) and writes
it into the same JSON files the live candle recorder uses
(``stocks/orb_candles_5min.json`` / ``orb_candles_15min.json``).

The write path is the recorder's own ``save_rows`` / ``save_rows_15``, so the
format stays identical (wide row per (date, symbol), ``price_<HHMM>_O/H/L/C``
columns) and there is no schema drift.  tvdatafeed returns *finalized* bars, so
this gives correct candle values on demand without waiting for the live
recorder's forming-bar snapshot + post-close backfill.

This module is a thin orchestrator: progress is stored in ``_PROGRESS`` for the
``/api/candles/manual-fetch/status`` endpoint to read.
"""
from __future__ import annotations

import datetime as dt
import threading
import time
from zoneinfo import ZoneInfo

from advance_orb.candle_recorder import (
    save_rows,
    save_rows_15,
    universe_symbols,
)

IST = ZoneInfo("Asia/Kolkata")

_PROGRESS: dict = {
    "running": False,
    "phase": None,             # "5" | "15" | None
    "current_symbol": None,
    "done": 0,
    "total": 0,
    "errors": 0,
    "saved": 0,
    "started_at": None,
    "finished_at": None,
    "last_message": None,
}
_LOCK = threading.Lock()

# Flush the accumulated rows to disk only every N symbols (file rewrite is the
# slow part); final leftovers are flushed at the end of each phase.
_FLUSH_EVERY = 25


def _label(ts: dt.datetime) -> str:
    return ts.strftime("%H%M")


def _fetch_symbol_ohlc(tv, symbol: str, exchange: str, interval,
                       n_bars: int) -> list[tuple[str, dict]]:
    """Return ``[(HHMM_label, {O, H, L, C}), ...]`` for one symbol.

    Only candles whose label falls at/after 09:15 are kept (anything earlier in
    the fetched window is pre-open/dummy data from TradingView).
    """
    try:
        df = tv.get_hist(symbol=symbol, exchange=exchange, interval=interval, n_bars=n_bars)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(str(exc)[:160]) from exc
    if df is None or df.empty:
        return []
    out: list[tuple[str, dict]] = []
    for ts, row in df.iterrows():
        if isinstance(ts, dt.datetime):
            t2 = ts.astimezone(IST) if ts.tzinfo else ts.replace(tzinfo=IST)
        else:
            t2 = dt.datetime.fromtimestamp(ts, tz=IST)
        lb = _label(t2)
        if t2.hour < 9 or (t2.hour == 9 and t2.minute < 15):
            continue
        o, h, l, c = row.get("open"), row.get("high"), row.get("low"), row.get("close")
        try:
            o, h, l, c = float(o), float(h), float(l), float(c)
        except (TypeError, ValueError):
            continue
        if any(v != v for v in (o, h, l, c)):  # NaN
            continue
        out.append((lb, {"price_%s_O" % lb: o, "price_%s_H" % lb: h,
                         "price_%s_L" % lb: l, "price_%s_C" % lb: c}))
    return out


def _run_timeframe(tf: int, universe: list[str], today: str,
                   workers: int) -> tuple[int, int]:
    """Fetch one timeframe (5 or 15) across the universe in parallel.

    ``workers`` TvDatafeed instances are created (one per pool worker) because
    each instance holds its own websocket; sharing a single instance across
    threads is not thread-safe.  Returns ``(saved, errors)``.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from tvDatafeed import TvDatafeed, Interval as TvInterval

    exchange = "NSE"
    interval = TvInterval.in_5_minute if tf == 5 else TvInterval.in_15_minute
    n_bars = 100 if tf == 5 else 40
    sink = save_rows if tf == 5 else save_rows_15

    with _LOCK:
        _PROGRESS.update(running=True, phase=str(tf), current_symbol=None,
                         done=0, total=len(universe),
                         last_message=f"Fetching {tf}-min candles ({workers} workers)…")

    # Thread-local TvDatafeed instance per worker.
    _tls = threading.local()

    def _worker_pool():
        if not hasattr(_tls, "_tv"):
            _tls._tv = TvDatafeed()
        return _tls._tv

    def _job(i, sym):
        try:
            bars = _fetch_symbol_ohlc(_worker_pool(), sym, exchange, interval, n_bars)
            return (i, sym, bars, None)
        except Exception as exc:  # noqa: BLE001
            return (i, sym, None, str(exc)[:160])

    saved = errors = done = 0
    batch: list[dict] = []
    results: list[tuple[int, str, list, str | None]] = []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_job, i, sym) for i, sym in enumerate(universe)]
        for fut in as_completed(futs):
            i, sym, bars, err = fut.result()
            done += 1
            if err is not None:
                errors += 1
                with _LOCK:
                    _PROGRESS.update(errors=errors, saved=saved,
                                     current_symbol=sym, done=done,
                                     last_message=f"{sym}: {err}")
                continue
            if bars:
                rec: dict = {"date": today, "symbol": sym}
                for _, flds in bars:
                    rec.update(flds)
                batch.append(rec)
                if len(batch) >= _FLUSH_EVERY:
                    saved += sink(batch)
                    batch = []
            with _LOCK:
                _PROGRESS.update(errors=errors, saved=saved,
                                 current_symbol=sym, done=done)
    if batch:
        saved += sink(batch)
        with _LOCK:
            _PROGRESS.update(errors=errors, saved=saved)
    return saved, errors


def run_job(timeframes=(5, 15), workers: int = 4) -> dict:
    """Background entrypoint.  Throws on universe/tvdatafeed load failure."""
    from advance_orb.candle_recorder import universe_symbols as _us
    universe = _us()
    if not universe:
        raise RuntimeError("no ORB universe from TradingView")

    today = dt.datetime.now(IST).strftime("%Y-%m-%d")
    saved = errors = 0
    for tf in timeframes:
        s, e = _run_timeframe(tf, universe, today, workers=workers)
        saved += s
        errors += e

    with _LOCK:
        _PROGRESS.update(running=False, phase=None, current_symbol=None, done=0,
                         total=0, saved=saved, errors=errors,
                         finished_at=dt.datetime.now(IST).isoformat(),
                         last_message=f"Done — saved {saved} candle rows, "
                                      f"{errors} errors")
    return dict(_PROGRESS)


def start_manual_fetch(timeframes=(5, 15), workers: int = 4) -> dict:
    """Start the background fetch job if none is running.  Returns status."""
    with _LOCK:
        if _PROGRESS["running"]:
            return dict(_PROGRESS) | {"already_running": True}
        _PROGRESS.update(running=True, phase="start", current_symbol=None,
                         done=0, total=0, errors=0, saved=0,
                         started_at=dt.datetime.now(IST).isoformat(),
                         finished_at=None, last_message="Starting…")

    threading.Thread(
        target=lambda: run_job(timeframes, workers),
        daemon=True,
        name="manual-candle-fetch",
    ).start()
    return {"started": True, "message": "Manual candle fetch started"}


def manual_fetch_status() -> dict:
    with _LOCK:
        return dict(_PROGRESS)
