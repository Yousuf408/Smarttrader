-- ================================================================
-- orb_candles_5min — 5-min candle recorder for the Advance ORB
-- TradingView universe (all stocks returned by the scan).
-- One wide row per (date, symbol). Columns per 5-min candle from
-- 09:15 -> 15:10 IST:  price_<HHMM>_O/H/L/C  +  vwap_<HHMM>.
-- 200-EMA and Change % stored ONLY for the 9:15 candle.
-- Recorder PATCHes the forming candle; end of day = full picture.
-- ================================================================
CREATE TABLE IF NOT EXISTS orb_candles_5min (
    id             BIGSERIAL PRIMARY KEY,
    date           DATE NOT NULL,
    symbol         TEXT NOT NULL,
    -- 9:15 candle only: ORB-driven factors for that stock
    ema200_915     NUMERIC(12,2),
    change_pct_915 NUMERIC(6,2),
    price_0915_O NUMERIC(12,2),  -- 09:15 O-candle
    price_0915_H NUMERIC(12,2),  -- 09:15 H-candle
    price_0915_L NUMERIC(12,2),  -- 09:15 L-candle
    price_0915_C NUMERIC(12,2),  -- 09:15 C-candle
    vwap_0915     NUMERIC(12,2),  -- 09:15 cumulative daily VWAP
    price_0920_O NUMERIC(12,2),  -- 09:20 O-candle
    price_0920_H NUMERIC(12,2),  -- 09:20 H-candle
    price_0920_L NUMERIC(12,2),  -- 09:20 L-candle
    price_0920_C NUMERIC(12,2),  -- 09:20 C-candle
    vwap_0920     NUMERIC(12,2),  -- 09:20 cumulative daily VWAP
    price_0925_O NUMERIC(12,2),  -- 09:25 O-candle
    price_0925_H NUMERIC(12,2),  -- 09:25 H-candle
    price_0925_L NUMERIC(12,2),  -- 09:25 L-candle
    price_0925_C NUMERIC(12,2),  -- 09:25 C-candle
    vwap_0925     NUMERIC(12,2),  -- 09:25 cumulative daily VWAP
    price_0930_O NUMERIC(12,2),  -- 09:30 O-candle
    price_0930_H NUMERIC(12,2),  -- 09:30 H-candle
    price_0930_L NUMERIC(12,2),  -- 09:30 L-candle
    price_0930_C NUMERIC(12,2),  -- 09:30 C-candle
    vwap_0930     NUMERIC(12,2),  -- 09:30 cumulative daily VWAP
    price_0935_O NUMERIC(12,2),  -- 09:35 O-candle
    price_0935_H NUMERIC(12,2),  -- 09:35 H-candle
    price_0935_L NUMERIC(12,2),  -- 09:35 L-candle
    price_0935_C NUMERIC(12,2),  -- 09:35 C-candle
    vwap_0935     NUMERIC(12,2),  -- 09:35 cumulative daily VWAP
    price_0940_O NUMERIC(12,2),  -- 09:40 O-candle
    price_0940_H NUMERIC(12,2),  -- 09:40 H-candle
    price_0940_L NUMERIC(12,2),  -- 09:40 L-candle
    price_0940_C NUMERIC(12,2),  -- 09:40 C-candle
    vwap_0940     NUMERIC(12,2),  -- 09:40 cumulative daily VWAP
    price_0945_O NUMERIC(12,2),  -- 09:45 O-candle
    price_0945_H NUMERIC(12,2),  -- 09:45 H-candle
    price_0945_L NUMERIC(12,2),  -- 09:45 L-candle
    price_0945_C NUMERIC(12,2),  -- 09:45 C-candle
    vwap_0945     NUMERIC(12,2),  -- 09:45 cumulative daily VWAP
    price_0950_O NUMERIC(12,2),  -- 09:50 O-candle
    price_0950_H NUMERIC(12,2),  -- 09:50 H-candle
    price_0950_L NUMERIC(12,2),  -- 09:50 L-candle
    price_0950_C NUMERIC(12,2),  -- 09:50 C-candle
    vwap_0950     NUMERIC(12,2),  -- 09:50 cumulative daily VWAP
    price_0955_O NUMERIC(12,2),  -- 09:55 O-candle
    price_0955_H NUMERIC(12,2),  -- 09:55 H-candle
    price_0955_L NUMERIC(12,2),  -- 09:55 L-candle
    price_0955_C NUMERIC(12,2),  -- 09:55 C-candle
    vwap_0955     NUMERIC(12,2),  -- 09:55 cumulative daily VWAP
    price_1000_O NUMERIC(12,2),  -- 10:00 O-candle
    price_1000_H NUMERIC(12,2),  -- 10:00 H-candle
    price_1000_L NUMERIC(12,2),  -- 10:00 L-candle
    price_1000_C NUMERIC(12,2),  -- 10:00 C-candle
    vwap_1000     NUMERIC(12,2),  -- 10:00 cumulative daily VWAP
    price_1005_O NUMERIC(12,2),  -- 10:05 O-candle
    price_1005_H NUMERIC(12,2),  -- 10:05 H-candle
    price_1005_L NUMERIC(12,2),  -- 10:05 L-candle
    price_1005_C NUMERIC(12,2),  -- 10:05 C-candle
    vwap_1005     NUMERIC(12,2),  -- 10:05 cumulative daily VWAP
    price_1010_O NUMERIC(12,2),  -- 10:10 O-candle
    price_1010_H NUMERIC(12,2),  -- 10:10 H-candle
    price_1010_L NUMERIC(12,2),  -- 10:10 L-candle
    price_1010_C NUMERIC(12,2),  -- 10:10 C-candle
    vwap_1010     NUMERIC(12,2),  -- 10:10 cumulative daily VWAP
    price_1015_O NUMERIC(12,2),  -- 10:15 O-candle
    price_1015_H NUMERIC(12,2),  -- 10:15 H-candle
    price_1015_L NUMERIC(12,2),  -- 10:15 L-candle
    price_1015_C NUMERIC(12,2),  -- 10:15 C-candle
    vwap_1015     NUMERIC(12,2),  -- 10:15 cumulative daily VWAP
    price_1020_O NUMERIC(12,2),  -- 10:20 O-candle
    price_1020_H NUMERIC(12,2),  -- 10:20 H-candle
    price_1020_L NUMERIC(12,2),  -- 10:20 L-candle
    price_1020_C NUMERIC(12,2),  -- 10:20 C-candle
    vwap_1020     NUMERIC(12,2),  -- 10:20 cumulative daily VWAP
    price_1025_O NUMERIC(12,2),  -- 10:25 O-candle
    price_1025_H NUMERIC(12,2),  -- 10:25 H-candle
    price_1025_L NUMERIC(12,2),  -- 10:25 L-candle
    price_1025_C NUMERIC(12,2),  -- 10:25 C-candle
    vwap_1025     NUMERIC(12,2),  -- 10:25 cumulative daily VWAP
    price_1030_O NUMERIC(12,2),  -- 10:30 O-candle
    price_1030_H NUMERIC(12,2),  -- 10:30 H-candle
    price_1030_L NUMERIC(12,2),  -- 10:30 L-candle
    price_1030_C NUMERIC(12,2),  -- 10:30 C-candle
    vwap_1030     NUMERIC(12,2),  -- 10:30 cumulative daily VWAP
    price_1035_O NUMERIC(12,2),  -- 10:35 O-candle
    price_1035_H NUMERIC(12,2),  -- 10:35 H-candle
    price_1035_L NUMERIC(12,2),  -- 10:35 L-candle
    price_1035_C NUMERIC(12,2),  -- 10:35 C-candle
    vwap_1035     NUMERIC(12,2),  -- 10:35 cumulative daily VWAP
    price_1040_O NUMERIC(12,2),  -- 10:40 O-candle
    price_1040_H NUMERIC(12,2),  -- 10:40 H-candle
    price_1040_L NUMERIC(12,2),  -- 10:40 L-candle
    price_1040_C NUMERIC(12,2),  -- 10:40 C-candle
    vwap_1040     NUMERIC(12,2),  -- 10:40 cumulative daily VWAP
    price_1045_O NUMERIC(12,2),  -- 10:45 O-candle
    price_1045_H NUMERIC(12,2),  -- 10:45 H-candle
    price_1045_L NUMERIC(12,2),  -- 10:45 L-candle
    price_1045_C NUMERIC(12,2),  -- 10:45 C-candle
    vwap_1045     NUMERIC(12,2),  -- 10:45 cumulative daily VWAP
    price_1050_O NUMERIC(12,2),  -- 10:50 O-candle
    price_1050_H NUMERIC(12,2),  -- 10:50 H-candle
    price_1050_L NUMERIC(12,2),  -- 10:50 L-candle
    price_1050_C NUMERIC(12,2),  -- 10:50 C-candle
    vwap_1050     NUMERIC(12,2),  -- 10:50 cumulative daily VWAP
    price_1055_O NUMERIC(12,2),  -- 10:55 O-candle
    price_1055_H NUMERIC(12,2),  -- 10:55 H-candle
    price_1055_L NUMERIC(12,2),  -- 10:55 L-candle
    price_1055_C NUMERIC(12,2),  -- 10:55 C-candle
    vwap_1055     NUMERIC(12,2),  -- 10:55 cumulative daily VWAP
    price_1100_O NUMERIC(12,2),  -- 11:00 O-candle
    price_1100_H NUMERIC(12,2),  -- 11:00 H-candle
    price_1100_L NUMERIC(12,2),  -- 11:00 L-candle
    price_1100_C NUMERIC(12,2),  -- 11:00 C-candle
    vwap_1100     NUMERIC(12,2),  -- 11:00 cumulative daily VWAP
    price_1105_O NUMERIC(12,2),  -- 11:05 O-candle
    price_1105_H NUMERIC(12,2),  -- 11:05 H-candle
    price_1105_L NUMERIC(12,2),  -- 11:05 L-candle
    price_1105_C NUMERIC(12,2),  -- 11:05 C-candle
    vwap_1105     NUMERIC(12,2),  -- 11:05 cumulative daily VWAP
    price_1110_O NUMERIC(12,2),  -- 11:10 O-candle
    price_1110_H NUMERIC(12,2),  -- 11:10 H-candle
    price_1110_L NUMERIC(12,2),  -- 11:10 L-candle
    price_1110_C NUMERIC(12,2),  -- 11:10 C-candle
    vwap_1110     NUMERIC(12,2),  -- 11:10 cumulative daily VWAP
    price_1115_O NUMERIC(12,2),  -- 11:15 O-candle
    price_1115_H NUMERIC(12,2),  -- 11:15 H-candle
    price_1115_L NUMERIC(12,2),  -- 11:15 L-candle
    price_1115_C NUMERIC(12,2),  -- 11:15 C-candle
    vwap_1115     NUMERIC(12,2),  -- 11:15 cumulative daily VWAP
    price_1120_O NUMERIC(12,2),  -- 11:20 O-candle
    price_1120_H NUMERIC(12,2),  -- 11:20 H-candle
    price_1120_L NUMERIC(12,2),  -- 11:20 L-candle
    price_1120_C NUMERIC(12,2),  -- 11:20 C-candle
    vwap_1120     NUMERIC(12,2),  -- 11:20 cumulative daily VWAP
    price_1125_O NUMERIC(12,2),  -- 11:25 O-candle
    price_1125_H NUMERIC(12,2),  -- 11:25 H-candle
    price_1125_L NUMERIC(12,2),  -- 11:25 L-candle
    price_1125_C NUMERIC(12,2),  -- 11:25 C-candle
    vwap_1125     NUMERIC(12,2),  -- 11:25 cumulative daily VWAP
    price_1130_O NUMERIC(12,2),  -- 11:30 O-candle
    price_1130_H NUMERIC(12,2),  -- 11:30 H-candle
    price_1130_L NUMERIC(12,2),  -- 11:30 L-candle
    price_1130_C NUMERIC(12,2),  -- 11:30 C-candle
    vwap_1130     NUMERIC(12,2),  -- 11:30 cumulative daily VWAP
    price_1135_O NUMERIC(12,2),  -- 11:35 O-candle
    price_1135_H NUMERIC(12,2),  -- 11:35 H-candle
    price_1135_L NUMERIC(12,2),  -- 11:35 L-candle
    price_1135_C NUMERIC(12,2),  -- 11:35 C-candle
    vwap_1135     NUMERIC(12,2),  -- 11:35 cumulative daily VWAP
    price_1140_O NUMERIC(12,2),  -- 11:40 O-candle
    price_1140_H NUMERIC(12,2),  -- 11:40 H-candle
    price_1140_L NUMERIC(12,2),  -- 11:40 L-candle
    price_1140_C NUMERIC(12,2),  -- 11:40 C-candle
    vwap_1140     NUMERIC(12,2),  -- 11:40 cumulative daily VWAP
    price_1145_O NUMERIC(12,2),  -- 11:45 O-candle
    price_1145_H NUMERIC(12,2),  -- 11:45 H-candle
    price_1145_L NUMERIC(12,2),  -- 11:45 L-candle
    price_1145_C NUMERIC(12,2),  -- 11:45 C-candle
    vwap_1145     NUMERIC(12,2),  -- 11:45 cumulative daily VWAP
    price_1150_O NUMERIC(12,2),  -- 11:50 O-candle
    price_1150_H NUMERIC(12,2),  -- 11:50 H-candle
    price_1150_L NUMERIC(12,2),  -- 11:50 L-candle
    price_1150_C NUMERIC(12,2),  -- 11:50 C-candle
    vwap_1150     NUMERIC(12,2),  -- 11:50 cumulative daily VWAP
    price_1155_O NUMERIC(12,2),  -- 11:55 O-candle
    price_1155_H NUMERIC(12,2),  -- 11:55 H-candle
    price_1155_L NUMERIC(12,2),  -- 11:55 L-candle
    price_1155_C NUMERIC(12,2),  -- 11:55 C-candle
    vwap_1155     NUMERIC(12,2),  -- 11:55 cumulative daily VWAP
    price_1200_O NUMERIC(12,2),  -- 12:00 O-candle
    price_1200_H NUMERIC(12,2),  -- 12:00 H-candle
    price_1200_L NUMERIC(12,2),  -- 12:00 L-candle
    price_1200_C NUMERIC(12,2),  -- 12:00 C-candle
    vwap_1200     NUMERIC(12,2),  -- 12:00 cumulative daily VWAP
    price_1205_O NUMERIC(12,2),  -- 12:05 O-candle
    price_1205_H NUMERIC(12,2),  -- 12:05 H-candle
    price_1205_L NUMERIC(12,2),  -- 12:05 L-candle
    price_1205_C NUMERIC(12,2),  -- 12:05 C-candle
    vwap_1205     NUMERIC(12,2),  -- 12:05 cumulative daily VWAP
    price_1210_O NUMERIC(12,2),  -- 12:10 O-candle
    price_1210_H NUMERIC(12,2),  -- 12:10 H-candle
    price_1210_L NUMERIC(12,2),  -- 12:10 L-candle
    price_1210_C NUMERIC(12,2),  -- 12:10 C-candle
    vwap_1210     NUMERIC(12,2),  -- 12:10 cumulative daily VWAP
    price_1215_O NUMERIC(12,2),  -- 12:15 O-candle
    price_1215_H NUMERIC(12,2),  -- 12:15 H-candle
    price_1215_L NUMERIC(12,2),  -- 12:15 L-candle
    price_1215_C NUMERIC(12,2),  -- 12:15 C-candle
    vwap_1215     NUMERIC(12,2),  -- 12:15 cumulative daily VWAP
    price_1220_O NUMERIC(12,2),  -- 12:20 O-candle
    price_1220_H NUMERIC(12,2),  -- 12:20 H-candle
    price_1220_L NUMERIC(12,2),  -- 12:20 L-candle
    price_1220_C NUMERIC(12,2),  -- 12:20 C-candle
    vwap_1220     NUMERIC(12,2),  -- 12:20 cumulative daily VWAP
    price_1225_O NUMERIC(12,2),  -- 12:25 O-candle
    price_1225_H NUMERIC(12,2),  -- 12:25 H-candle
    price_1225_L NUMERIC(12,2),  -- 12:25 L-candle
    price_1225_C NUMERIC(12,2),  -- 12:25 C-candle
    vwap_1225     NUMERIC(12,2),  -- 12:25 cumulative daily VWAP
    price_1230_O NUMERIC(12,2),  -- 12:30 O-candle
    price_1230_H NUMERIC(12,2),  -- 12:30 H-candle
    price_1230_L NUMERIC(12,2),  -- 12:30 L-candle
    price_1230_C NUMERIC(12,2),  -- 12:30 C-candle
    vwap_1230     NUMERIC(12,2),  -- 12:30 cumulative daily VWAP
    price_1235_O NUMERIC(12,2),  -- 12:35 O-candle
    price_1235_H NUMERIC(12,2),  -- 12:35 H-candle
    price_1235_L NUMERIC(12,2),  -- 12:35 L-candle
    price_1235_C NUMERIC(12,2),  -- 12:35 C-candle
    vwap_1235     NUMERIC(12,2),  -- 12:35 cumulative daily VWAP
    price_1240_O NUMERIC(12,2),  -- 12:40 O-candle
    price_1240_H NUMERIC(12,2),  -- 12:40 H-candle
    price_1240_L NUMERIC(12,2),  -- 12:40 L-candle
    price_1240_C NUMERIC(12,2),  -- 12:40 C-candle
    vwap_1240     NUMERIC(12,2),  -- 12:40 cumulative daily VWAP
    price_1245_O NUMERIC(12,2),  -- 12:45 O-candle
    price_1245_H NUMERIC(12,2),  -- 12:45 H-candle
    price_1245_L NUMERIC(12,2),  -- 12:45 L-candle
    price_1245_C NUMERIC(12,2),  -- 12:45 C-candle
    vwap_1245     NUMERIC(12,2),  -- 12:45 cumulative daily VWAP
    price_1250_O NUMERIC(12,2),  -- 12:50 O-candle
    price_1250_H NUMERIC(12,2),  -- 12:50 H-candle
    price_1250_L NUMERIC(12,2),  -- 12:50 L-candle
    price_1250_C NUMERIC(12,2),  -- 12:50 C-candle
    vwap_1250     NUMERIC(12,2),  -- 12:50 cumulative daily VWAP
    price_1255_O NUMERIC(12,2),  -- 12:55 O-candle
    price_1255_H NUMERIC(12,2),  -- 12:55 H-candle
    price_1255_L NUMERIC(12,2),  -- 12:55 L-candle
    price_1255_C NUMERIC(12,2),  -- 12:55 C-candle
    vwap_1255     NUMERIC(12,2),  -- 12:55 cumulative daily VWAP
    price_1300_O NUMERIC(12,2),  -- 13:00 O-candle
    price_1300_H NUMERIC(12,2),  -- 13:00 H-candle
    price_1300_L NUMERIC(12,2),  -- 13:00 L-candle
    price_1300_C NUMERIC(12,2),  -- 13:00 C-candle
    vwap_1300     NUMERIC(12,2),  -- 13:00 cumulative daily VWAP
    price_1305_O NUMERIC(12,2),  -- 13:05 O-candle
    price_1305_H NUMERIC(12,2),  -- 13:05 H-candle
    price_1305_L NUMERIC(12,2),  -- 13:05 L-candle
    price_1305_C NUMERIC(12,2),  -- 13:05 C-candle
    vwap_1305     NUMERIC(12,2),  -- 13:05 cumulative daily VWAP
    price_1310_O NUMERIC(12,2),  -- 13:10 O-candle
    price_1310_H NUMERIC(12,2),  -- 13:10 H-candle
    price_1310_L NUMERIC(12,2),  -- 13:10 L-candle
    price_1310_C NUMERIC(12,2),  -- 13:10 C-candle
    vwap_1310     NUMERIC(12,2),  -- 13:10 cumulative daily VWAP
    price_1315_O NUMERIC(12,2),  -- 13:15 O-candle
    price_1315_H NUMERIC(12,2),  -- 13:15 H-candle
    price_1315_L NUMERIC(12,2),  -- 13:15 L-candle
    price_1315_C NUMERIC(12,2),  -- 13:15 C-candle
    vwap_1315     NUMERIC(12,2),  -- 13:15 cumulative daily VWAP
    price_1320_O NUMERIC(12,2),  -- 13:20 O-candle
    price_1320_H NUMERIC(12,2),  -- 13:20 H-candle
    price_1320_L NUMERIC(12,2),  -- 13:20 L-candle
    price_1320_C NUMERIC(12,2),  -- 13:20 C-candle
    vwap_1320     NUMERIC(12,2),  -- 13:20 cumulative daily VWAP
    price_1325_O NUMERIC(12,2),  -- 13:25 O-candle
    price_1325_H NUMERIC(12,2),  -- 13:25 H-candle
    price_1325_L NUMERIC(12,2),  -- 13:25 L-candle
    price_1325_C NUMERIC(12,2),  -- 13:25 C-candle
    vwap_1325     NUMERIC(12,2),  -- 13:25 cumulative daily VWAP
    price_1330_O NUMERIC(12,2),  -- 13:30 O-candle
    price_1330_H NUMERIC(12,2),  -- 13:30 H-candle
    price_1330_L NUMERIC(12,2),  -- 13:30 L-candle
    price_1330_C NUMERIC(12,2),  -- 13:30 C-candle
    vwap_1330     NUMERIC(12,2),  -- 13:30 cumulative daily VWAP
    price_1335_O NUMERIC(12,2),  -- 13:35 O-candle
    price_1335_H NUMERIC(12,2),  -- 13:35 H-candle
    price_1335_L NUMERIC(12,2),  -- 13:35 L-candle
    price_1335_C NUMERIC(12,2),  -- 13:35 C-candle
    vwap_1335     NUMERIC(12,2),  -- 13:35 cumulative daily VWAP
    price_1340_O NUMERIC(12,2),  -- 13:40 O-candle
    price_1340_H NUMERIC(12,2),  -- 13:40 H-candle
    price_1340_L NUMERIC(12,2),  -- 13:40 L-candle
    price_1340_C NUMERIC(12,2),  -- 13:40 C-candle
    vwap_1340     NUMERIC(12,2),  -- 13:40 cumulative daily VWAP
    price_1345_O NUMERIC(12,2),  -- 13:45 O-candle
    price_1345_H NUMERIC(12,2),  -- 13:45 H-candle
    price_1345_L NUMERIC(12,2),  -- 13:45 L-candle
    price_1345_C NUMERIC(12,2),  -- 13:45 C-candle
    vwap_1345     NUMERIC(12,2),  -- 13:45 cumulative daily VWAP
    price_1350_O NUMERIC(12,2),  -- 13:50 O-candle
    price_1350_H NUMERIC(12,2),  -- 13:50 H-candle
    price_1350_L NUMERIC(12,2),  -- 13:50 L-candle
    price_1350_C NUMERIC(12,2),  -- 13:50 C-candle
    vwap_1350     NUMERIC(12,2),  -- 13:50 cumulative daily VWAP
    price_1355_O NUMERIC(12,2),  -- 13:55 O-candle
    price_1355_H NUMERIC(12,2),  -- 13:55 H-candle
    price_1355_L NUMERIC(12,2),  -- 13:55 L-candle
    price_1355_C NUMERIC(12,2),  -- 13:55 C-candle
    vwap_1355     NUMERIC(12,2),  -- 13:55 cumulative daily VWAP
    price_1400_O NUMERIC(12,2),  -- 14:00 O-candle
    price_1400_H NUMERIC(12,2),  -- 14:00 H-candle
    price_1400_L NUMERIC(12,2),  -- 14:00 L-candle
    price_1400_C NUMERIC(12,2),  -- 14:00 C-candle
    vwap_1400     NUMERIC(12,2),  -- 14:00 cumulative daily VWAP
    price_1405_O NUMERIC(12,2),  -- 14:05 O-candle
    price_1405_H NUMERIC(12,2),  -- 14:05 H-candle
    price_1405_L NUMERIC(12,2),  -- 14:05 L-candle
    price_1405_C NUMERIC(12,2),  -- 14:05 C-candle
    vwap_1405     NUMERIC(12,2),  -- 14:05 cumulative daily VWAP
    price_1410_O NUMERIC(12,2),  -- 14:10 O-candle
    price_1410_H NUMERIC(12,2),  -- 14:10 H-candle
    price_1410_L NUMERIC(12,2),  -- 14:10 L-candle
    price_1410_C NUMERIC(12,2),  -- 14:10 C-candle
    vwap_1410     NUMERIC(12,2),  -- 14:10 cumulative daily VWAP
    price_1415_O NUMERIC(12,2),  -- 14:15 O-candle
    price_1415_H NUMERIC(12,2),  -- 14:15 H-candle
    price_1415_L NUMERIC(12,2),  -- 14:15 L-candle
    price_1415_C NUMERIC(12,2),  -- 14:15 C-candle
    vwap_1415     NUMERIC(12,2),  -- 14:15 cumulative daily VWAP
    price_1420_O NUMERIC(12,2),  -- 14:20 O-candle
    price_1420_H NUMERIC(12,2),  -- 14:20 H-candle
    price_1420_L NUMERIC(12,2),  -- 14:20 L-candle
    price_1420_C NUMERIC(12,2),  -- 14:20 C-candle
    vwap_1420     NUMERIC(12,2),  -- 14:20 cumulative daily VWAP
    price_1425_O NUMERIC(12,2),  -- 14:25 O-candle
    price_1425_H NUMERIC(12,2),  -- 14:25 H-candle
    price_1425_L NUMERIC(12,2),  -- 14:25 L-candle
    price_1425_C NUMERIC(12,2),  -- 14:25 C-candle
    vwap_1425     NUMERIC(12,2),  -- 14:25 cumulative daily VWAP
    price_1430_O NUMERIC(12,2),  -- 14:30 O-candle
    price_1430_H NUMERIC(12,2),  -- 14:30 H-candle
    price_1430_L NUMERIC(12,2),  -- 14:30 L-candle
    price_1430_C NUMERIC(12,2),  -- 14:30 C-candle
    vwap_1430     NUMERIC(12,2),  -- 14:30 cumulative daily VWAP
    price_1435_O NUMERIC(12,2),  -- 14:35 O-candle
    price_1435_H NUMERIC(12,2),  -- 14:35 H-candle
    price_1435_L NUMERIC(12,2),  -- 14:35 L-candle
    price_1435_C NUMERIC(12,2),  -- 14:35 C-candle
    vwap_1435     NUMERIC(12,2),  -- 14:35 cumulative daily VWAP
    price_1440_O NUMERIC(12,2),  -- 14:40 O-candle
    price_1440_H NUMERIC(12,2),  -- 14:40 H-candle
    price_1440_L NUMERIC(12,2),  -- 14:40 L-candle
    price_1440_C NUMERIC(12,2),  -- 14:40 C-candle
    vwap_1440     NUMERIC(12,2),  -- 14:40 cumulative daily VWAP
    price_1445_O NUMERIC(12,2),  -- 14:45 O-candle
    price_1445_H NUMERIC(12,2),  -- 14:45 H-candle
    price_1445_L NUMERIC(12,2),  -- 14:45 L-candle
    price_1445_C NUMERIC(12,2),  -- 14:45 C-candle
    vwap_1445     NUMERIC(12,2),  -- 14:45 cumulative daily VWAP
    price_1450_O NUMERIC(12,2),  -- 14:50 O-candle
    price_1450_H NUMERIC(12,2),  -- 14:50 H-candle
    price_1450_L NUMERIC(12,2),  -- 14:50 L-candle
    price_1450_C NUMERIC(12,2),  -- 14:50 C-candle
    vwap_1450     NUMERIC(12,2),  -- 14:50 cumulative daily VWAP
    price_1455_O NUMERIC(12,2),  -- 14:55 O-candle
    price_1455_H NUMERIC(12,2),  -- 14:55 H-candle
    price_1455_L NUMERIC(12,2),  -- 14:55 L-candle
    price_1455_C NUMERIC(12,2),  -- 14:55 C-candle
    vwap_1455     NUMERIC(12,2),  -- 14:55 cumulative daily VWAP
    price_1500_O NUMERIC(12,2),  -- 15:00 O-candle
    price_1500_H NUMERIC(12,2),  -- 15:00 H-candle
    price_1500_L NUMERIC(12,2),  -- 15:00 L-candle
    price_1500_C NUMERIC(12,2),  -- 15:00 C-candle
    vwap_1500     NUMERIC(12,2),  -- 15:00 cumulative daily VWAP
    price_1505_O NUMERIC(12,2),  -- 15:05 O-candle
    price_1505_H NUMERIC(12,2),  -- 15:05 H-candle
    price_1505_L NUMERIC(12,2),  -- 15:05 L-candle
    price_1505_C NUMERIC(12,2),  -- 15:05 C-candle
    vwap_1505     NUMERIC(12,2),  -- 15:05 cumulative daily VWAP
    price_1510_O NUMERIC(12,2),  -- 15:10 O-candle
    price_1510_H NUMERIC(12,2),  -- 15:10 H-candle
    price_1510_L NUMERIC(12,2),  -- 15:10 L-candle
    price_1510_C NUMERIC(12,2),  -- 15:10 C-candle
        vwap_1510     NUMERIC(12,2)  -- 15:10 cumulative daily VWAP
,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (date, symbol)
);

CREATE INDEX IF NOT EXISTS orb_candles_5min_symbol_date_idx
    ON orb_candles_5min (symbol, date);

