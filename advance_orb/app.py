from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from tradingview_screener import Query, col
import pandas as pd

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

@app.get("/")
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
                "columns": ["Symbol", "Price", "CHG%", "GAP%", "Volume", "RELVOL", "Sector"],
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
                "columns": ["Symbol", "Price", "CHG%", "GAP%", "Volume", "RELVOL", "Sector"],
                "message": f"No stocks with gap < {GAP_THRESHOLD}%"
            }

        # ─── Step 3: Format Data for Frontend ───
        result = []
        for _, row in df.iterrows():
            symbol = row['name']
            
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
                "Sector": row.get('sector', 'Unknown')
            })

        return {
            "strategy": "advanceorb",
            "name": "Advance ORB",
            "count": len(result),
            "data": result,
            "columns": ["Symbol", "Price", "CHG%", "GAP%", "Volume", "RELVOL", "Sector"],
            "conditions": {
                "price": f"{PRICE_MIN} to {PRICE_MAX} INR",
                "gap": f"< {GAP_THRESHOLD}%",
                "market_cap": f"> {MARKET_CAP_MIN/1e9:.0f}B INR",
                "exchange": "NSE"
            }
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/health")
def health():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
