-- 5-minute ORB Candles Schema for Supabase / PostgreSQL
CREATE TABLE IF NOT EXISTS orb_candles_5min (
    id BIGSERIAL PRIMARY KEY,
    symbol VARCHAR(30) NOT NULL,
    candle_time TIMESTAMP WITH TIME ZONE NOT NULL,
    open_price NUMERIC(10, 2) NOT NULL,
    high_price NUMERIC(10, 2) NOT NULL,
    low_price NUMERIC(10, 2) NOT NULL,
    close_price NUMERIC(10, 2) NOT NULL,
    volume BIGINT DEFAULT 0,
    vwap NUMERIC(10, 2),
    ema_200 NUMERIC(10, 2),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orb_candles_symbol_time ON orb_candles_5min (symbol, candle_time DESC);
