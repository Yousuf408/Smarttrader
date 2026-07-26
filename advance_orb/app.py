from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from tradingview_screener import Query, col
from tradingview_screener.query import HEADERS as TV_HEADERS
import pandas as pd
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from broker.dhan import calculate_max_qty_batch

app = FastAPI(
    title="TradeAlgo Pro - Advance ORB",
    description="Fetches NSE stocks with price 200-3000, gap < 2%, market cap > 41B",
    version="1.0.0"
)

# CORS - Allow frontend to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── HARDCODED CONDITIONS ───
PRICE_MIN = 200
PRICE_MAX = 3000
GAP_THRESHOLD = 2.0
MARKET_CAP_MIN = 41_000_000_000  # 41 Billion INR
SMALL_CANDLE_THRESHOLD = 1.5
IST = ZoneInfo("Asia/Kolkata")
MAX_TV_STOCKS = 200
YFINANCE_WORKERS = 8
ADVANCE_ORB_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "GAP%",
    "Volume",
    "RELVOL",
    "Sector",
    "Small Candle",
    "MaxQty",
]


def has_small_opening_candle(symbol: str) -> bool:
    """Return whether the latest available 9:15 IST five-minute candle is small."""
    ticker = f"{str(symbol).strip().upper()}.NS"
    try:
        candles = yf.download(
            tickers=ticker,
            period="4d",
            interval="5m",
            progress=False,
            auto_adjust=False,
            prepost=False,
            threads=False,
        )
    except Exception:
        return False

    if candles.empty:
        return False

    # yfinance can return a MultiIndex even for one ticker.
    if isinstance(candles.columns, pd.MultiIndex):
        try:
            candles = candles.xs(ticker, axis=1, level=-1)
        except (KeyError, IndexError):
            try:
                candles = candles.xs(ticker, axis=1, level=0)
            except (KeyError, IndexError):
                return False

    if "High" not in candles or "Low" not in candles:
        return False

    local_index = pd.DatetimeIndex(candles.index)
    if local_index.tz is None:
        local_index = local_index.tz_localize(IST)
    else:
        local_index = local_index.tz_convert(IST)
    candles = candles.copy()
    candles.index = local_index

    opening_candles = candles[
        (candles.index.hour == 9) & (candles.index.minute == 15)
    ]
    if opening_candles.empty:
        return False

    candle = opening_candles.iloc[-1]
    high = pd.to_numeric(candle["High"], errors="coerce")
    low = pd.to_numeric(candle["Low"], errors="coerce")
    if pd.isna(high) or pd.isna(low) or low <= 0:
        return False

    candle_range = (high - low) / low * 100
    return candle_range <= SMALL_CANDLE_THRESHOLD


def filter_small_opening_candles(symbols: list[str]) -> set[str]:
    """Yahoo Finance 9:15 IST candle check, executed across many symbols in parallel.

    Each unique ticker is fetched with `yf.download` inside a thread pool. Any
    failure (delisted ticker, missing data, rate limit, exception) is treated
    as "not a small opening candle" so the symbol is excluded from results.
    """
    if not symbols:
        return set()

    unique = [str(s).strip().upper() for s in symbols if s]
    matches: set[str] = set()

    with ThreadPoolExecutor(max_workers=YFINANCE_WORKERS) as pool:
        futures = {pool.submit(has_small_opening_candle, sym): sym for sym in unique}
        for future in as_completed(futures):
            try:
                if future.result():
                    matches.add(futures[future])
            except Exception:
                continue
    return matches


@app.get("/api")
def root():
    return {
        "status": "ok",
        "message": "Advance ORB Strategy API",
        "conditions": {
            "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
            "gap": f"< {GAP_THRESHOLD}%",
            "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
            "exchange": "NSE"
        }
    }

@app.get("/api/strategies/advanceorb")
def get_advance_orb():
    """
    Fetch stocks from TradingView with 4 conditions:
    1. Price: 200 to 3000 INR
    2. Gap: < 2%
    3. Market Cap: > 41B INR
    4. Exchange: NSE
    """
    try:
        # ─── Step 1: Fetch from TradingView ───
        # Single POST to TradingView's scan endpoint, capped to MAX_TV_STOCKS
        # rows (≈4 pages of the default 50-row page size).
        tv_columns = [
            'name', 'close', 'change', 'gap', 'volume',
            'relative_volume', 'market_cap_basic', 'sector',
        ]
        tv_query = (Query()
            .select(*tv_columns)
            .set_markets('india')
            .where(
                col('close') > PRICE_MIN,
                col('close') <= PRICE_MAX,
                col('market_cap_basic') > MARKET_CAP_MIN,
                col('exchange') == 'NSE'
            )
            .order_by('change', ascending=False)
        )

        body = dict(tv_query.query)
        body['range'] = [0, MAX_TV_STOCKS]
        response = requests.post(
            tv_query.url,
            json=body,
            headers=TV_HEADERS,
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        total = int(payload.get('totalCount') or 0)

        raw_rows: list[dict] = []
        for symbol_row in (payload.get('data') or [])[:MAX_TV_STOCKS]:
            values = symbol_row.get('d') or []
            ticker = symbol_row.get('s')
            raw_rows.append(dict(zip(['ticker', *tv_columns], [ticker, *values])))

        df = pd.DataFrame(raw_rows)
        count = min(total, MAX_TV_STOCKS)

        if count == 0:
            return {
                "strategy": "advanceorb",
                "name": "Advance ORB",
                "count": 0,
                "data": [],
                "columns": ADVANCE_ORB_COLUMNS,
                "message": "No stocks found matching the conditions"
            }

        # ─── Step 2: Apply Gap Filter (< 2%) ───
        df['gap'] = pd.to_numeric(df['gap'], errors='coerce')
        df = df[df['gap'].notna() & (abs(df['gap']) < GAP_THRESHOLD)]

        if df.empty:
            return {
                "strategy": "advanceorb",
                "name": "Advance ORB",
                "count": 0,
                "data": [],
                "columns": ADVANCE_ORB_COLUMNS,
                "message": f"No stocks with gap < {GAP_THRESHOLD}%"
            }

        # ─── Step 3: Format Data for Frontend ───
        # Run the Yahoo Finance candle check across every candidate in parallel,
        # then keep only rows whose ticker is in the matching set.
        candidate_symbols = df['name'].dropna().astype(str).tolist()
        small_candle_symbols = filter_small_opening_candles(candidate_symbols)

        result = []
        for _, row in df.iterrows():
            symbol = row['name']
            if symbol not in small_candle_symbols:
                continue

            # Format volume
            vol = row.get('volume', 0)
            if vol >= 1_000_000:
                volume_str = f"{vol/1_000_000:.1f}M"
            elif vol >= 1_000:
                volume_str = f"{vol/1_000:.1f}K"
            else:
                volume_str = str(vol)

            # Format relative volume
            relvol = row.get('relative_volume', 0)
            relvol_str = f"{relvol:.2f}x" if pd.notna(relvol) else "0x"

            result.append({
                "Symbol": symbol,
                "Price": round(row['close'], 2),
                "CHG%": round(row['change'], 2),
                "GAP%": round(row['gap'], 2),
                "Volume": volume_str,
                "RELVOL": relvol_str,
                "Sector": row.get('sector', 'Unknown'),
                "Small Candle": "✓",
            })
             # ─── Step 4: Calculate MaxQty for first 20 stocks ───
        total_capital = 60000  # Default, user can change via settings
        num_parts = 4
        result = calculate_max_qty_batch(result, total_capital, num_parts)

        return {
            "strategy": "advanceorb",
            "name": "Advance ORB",
            "count": len(result),
            "data": result,
            "columns": ADVANCE_ORB_COLUMNS,
            "conditions": {
                "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
                "gap": f"< {GAP_THRESHOLD}%",
                "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
                "exchange": "NSE",
                "small_candle": f"9:15 IST range <= {SMALL_CANDLE_THRESHOLD}%",
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/health")
def health():
    return {"status": "healthy"}


@app.get("/api/strategies/advanceorb/refresh")
def refresh_advance_orb(tickers: str = ""):
    """Lightweight refresh of price/volume/change for a fixed list of tickers.

    Skips the Yahoo candle check so it stays around ~0.5–1.5 s even for
    ~150 symbols. Returns refreshed values per symbol; symbols TradingView
    cannot resolve are simply omitted from the response.
    """
    try:
        symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
        if not symbols:
            return {"refreshed": []}

        tv_query = (Query()
            .select('name', 'close', 'change', 'volume', 'relative_volume')
            .set_markets('india')
            .where(col('exchange') == 'NSE')
            .set_tickers(*[f'NSE:{sym}' for sym in symbols])
        )

        body = dict(tv_query.query)
        body['range'] = [0, max(50, len(symbols) + 10)]
        response = requests.post(
            tv_query.url,
            json=body,
            headers=TV_HEADERS,
            timeout=20,
        )
        response.raise_for_status()
        payload = response.json()

        refreshed: list[dict] = []
        for symbol_row in (payload.get('data') or []):
            values = symbol_row.get('d') or []
            if len(values) < 5:
                continue
            name = values[0]
            if not name:
                continue
            close = pd.to_numeric(values[1], errors='coerce')
            change = pd.to_numeric(values[2], errors='coerce')
            vol = pd.to_numeric(values[3], errors='coerce')
            relvol = pd.to_numeric(values[4], errors='coerce')

            if pd.notna(vol) and vol >= 1_000_000:
                volume_str = f"{vol/1_000_000:.1f}M"
            elif pd.notna(vol) and vol >= 1_000:
                volume_str = f"{vol/1_000:.1f}K"
            elif pd.notna(vol):
                volume_str = str(int(vol))
            else:
                volume_str = "0"
            relvol_str = f"{relvol:.2f}x" if pd.notna(relvol) else "0x"

            refreshed.append({
                "Symbol": name,
                "Price": round(float(close), 2) if pd.notna(close) else None,
                "CHG%": round(float(change), 2) if pd.notna(change) else None,
                "Volume": volume_str,
                "RELVOL": relvol_str,
            })

        return {"refreshed": refreshed}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# Serve only the frontend assets from the same origin as the API. Do not expose
# the repository root, which also contains backend source and project metadata.
PROJECT_ROOT = Path(__file__).resolve().parent.parent


@app.get("/", include_in_schema=False)
def frontend():
    return FileResponse(PROJECT_ROOT / "index.html", media_type="text/html")


@app.get("/style.css", include_in_schema=False)
def stylesheet():
    return FileResponse(PROJECT_ROOT / "style.css", media_type="text/css")


app.mount("/js", StaticFiles(directory=PROJECT_ROOT / "js"), name="frontend-js")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
