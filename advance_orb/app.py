from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from tradingview_screener import Query, col
import pandas as pd
import yfinance as yf

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
ADVANCE_ORB_COLUMNS = [
    "Symbol",
    "Price",
    "CHG%",
    "GAP%",
    "Volume",
    "RELVOL",
    "Sector",
    "Small Candle",
]


def has_small_opening_candle(symbol: str) -> bool:
    """Return whether the latest available 9:15 IST five-minute candle is small."""
    ticker = f"{str(symbol).strip().upper()}.NS"
    try:
        candles = yf.download(
            tickers=ticker,
            period="5d",
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
        count, df = (Query()
            .select(
                'name',           # Stock name
                'close',          # Current price
                'change',         # Change %
                'gap',            # Gap %
                'volume',         # Volume
                'relative_volume',# Relative volume
                'market_cap_basic',# Market cap
                'sector'          # Sector
            )
            .set_markets('india')
            .where(
                col('close') > PRICE_MIN,
                col('close') <= PRICE_MAX,
                col('market_cap_basic') > MARKET_CAP_MIN,
                col('exchange') == 'NSE'
            )
            .order_by('change', ascending=False)
            .get_scanner_data()
        )

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
        result = []
        for _, row in df.iterrows():
            symbol = row['name']
            small_candle = has_small_opening_candle(symbol)
            if not small_candle:
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
                "Small Candle": "✓" if small_candle else "✗",
            })

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
