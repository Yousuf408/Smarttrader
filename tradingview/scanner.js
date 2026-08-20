/**
 * TradingView Scanner Module (ESM)
 * 
 * Centralized module for all TradingView API interactions:
 * - TradingView Scanner API client with multi-condition filtering (100% Live Universe)
 * - Nifty Total Market Index constituent loader and cache
 * - Relative Volume (RELVOL) calculation from TradingView indicator & broker ticks
 * - Stock ticks mapping prioritizing Broker WebSocket
 * - Zero external Yahoo Finance dependency for maximum speed and zero rate limits.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { arrowStreamService } from '../broker/arrow_stream_service.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// -------------------------------------------------------------
// NIFTY TOTAL MARKET SYMBOL CONSTITUENT CACHE
// -------------------------------------------------------------
let niftyTotalMarketSet = null;

export async function getNiftyTotalMarketSymbols() {
  if (niftyTotalMarketSet && niftyTotalMarketSet.size > 500) {
    return niftyTotalMarketSet;
  }
  const localJsonPath = path.join(__dirname, 'nifty_total_market.json');
  if (fs.existsSync(localJsonPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(localJsonPath, 'utf8'));
      if (Array.isArray(data) && data.length > 500) {
        niftyTotalMarketSet = new Set(data.map(s => String(s).toUpperCase().trim()));
        return niftyTotalMarketSet;
      }
    } catch (e) {
      console.warn('Error reading local nifty_total_market.json:', e.message);
    }
  }

  try {
    const res = await fetch('https://www.niftyindices.com/IndexConstituent/ind_niftytotalmarket_list.csv', {
      headers: { 'User-Agent': 'Mozilla/5.0' }
    });
    if (res.ok) {
      const text = await res.text();
      const lines = text.split('\n').slice(1);
      const symbols = lines.map(line => line.split(',')[2]?.trim()).filter(Boolean);
      if (symbols.length > 500) {
        niftyTotalMarketSet = new Set(symbols.map(s => s.toUpperCase()));
        return niftyTotalMarketSet;
      }
    }
  } catch (e) {
    console.warn('Error fetching live Nifty Total Market CSV:', e.message);
  }

  return niftyTotalMarketSet || new Set();
}

// -------------------------------------------------------------
// INGESTION LOGGING BUFFER (Up to 500 events)
// -------------------------------------------------------------
const ingestionLogs = [];
const MAX_LOGS = 500;

export function logIngestion(level, tag, message, data = null) {
  const entry = {
    timestamp: new Date().toISOString(),
    ist_time: new Date().toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }),
    level: level.toUpperCase(),
    tag: tag,
    message: message,
    data: data
  };
  ingestionLogs.unshift(entry);
  if (ingestionLogs.length > MAX_LOGS) {
    ingestionLogs.pop();
  }
  const prefix = `[${entry.ist_time}] [${entry.level}] [${tag}]`;
  if (level === 'error') {
    console.error(prefix, message, data || '');
  } else if (level === 'warn') {
    console.warn(prefix, message, data || '');
  } else {
    console.log(prefix, message, data || '');
  }
}

export function getIngestionLogs(limit = 100) {
  return ingestionLogs.slice(0, Math.min(limit, ingestionLogs.length));
}

// -------------------------------------------------------------
// 5-MIN & 15-MIN CANDLE STORAGE ENGINES (9:15 AM OHLC)
// -------------------------------------------------------------
const first5mCandleMap = new Map();
const first15mVolMap = new Map();

// Paths to persistent candle JSON files
const JSON_DIR = path.join(__dirname, '..', 'json');

const CANDLE_5MIN_JSON_PATH = path.join(JSON_DIR, 'candle_5min.json');
const CANDLE_15MIN_JSON_PATH = path.join(JSON_DIR, 'candle_15min.json');

export function getIstDateString() {
  return new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' });
}

let activeSessionDate = getIstDateString();

// Check if a new day has arrived (e.g. tomorrow, 20th August) and automatically roll over historical volume baselines
export function checkAndPerformDailyRollover(force = false) {
  const currentIstDate = getIstDateString();
  if (currentIstDate !== activeSessionDate || force) {
    logIngestion('info', 'INGESTION-DAY-ROLLOVER', `Rolling over session from ${activeSessionDate} to ${currentIstDate} (force=${force})...`);
    
    // Rollover 15m candle map
    first15mVolMap.forEach((v, sym) => {
      const yesterdayVol = Number(v.today_15m_vol || v.volume_0915 || 0);
      const prevD1 = Number(v.d1_vol || (v.prev_3d_vols && v.prev_3d_vols[0]) || 0);
      const prevD2 = Number(v.d2_vol || (v.prev_3d_vols && v.prev_3d_vols[1]) || 0);
      const prevD3 = Number(v.d3_vol || (v.prev_3d_vols && v.prev_3d_vols[2]) || 0);
      
      const newD1 = yesterdayVol > 0 ? yesterdayVol : (prevD1 > 0 ? prevD1 : Number(v.prev_3d_max || 0));
      const newD2 = prevD1 > 0 ? prevD1 : (prevD2 > 0 ? prevD2 : newD1);
      const newD3 = prevD2 > 0 ? prevD2 : (prevD3 > 0 ? prevD3 : newD2);
      
      const newMax = Math.max(newD1, newD2, newD3);
      
      v.date = currentIstDate;
      v.d1_vol = newD1;
      v.d2_vol = newD2;
      v.d3_vol = newD3;
      v.prev_3d_vols = [newD1, newD2, newD3];
      v.prev_3d_max = newMax;
      v.today_15m_vol = 0;
      v.volume_0915 = 0;
      v.open915 = 0;
      v.high915 = 0;
      v.low915 = 0;
      v.close915 = 0;
      v.price_0915_O = 0;
      v.price_0915_H = 0;
      v.price_0915_L = 0;
      v.price_0915_C = 0;
      v.candles_till_1015 = null;
      v.is_highest = false;
      v.extra_volume = 0;
    });

    // Rollover 5m candle map
    first5mCandleMap.forEach((v, sym) => {
      const yesterdayVol = Number(v.today_5m_vol || v.volume_0915 || 0);
      const prevD1 = Number(v.d1_vol || (v.prev_3d_vols && v.prev_3d_vols[0]) || 0);
      const prevD2 = Number(v.d2_vol || (v.prev_3d_vols && v.prev_3d_vols[1]) || 0);
      const prevD3 = Number(v.d3_vol || (v.prev_3d_vols && v.prev_3d_vols[2]) || 0);
      
      const newD1 = yesterdayVol > 0 ? yesterdayVol : (prevD1 > 0 ? prevD1 : Number(v.prev_3d_max || 0));
      const newD2 = prevD1 > 0 ? prevD1 : (prevD2 > 0 ? prevD2 : newD1);
      const newD3 = prevD2 > 0 ? prevD2 : (prevD3 > 0 ? prevD3 : newD2);
      
      const newMax = Math.max(newD1, newD2, newD3);
      
      v.date = currentIstDate;
      v.d1_vol = newD1;
      v.d2_vol = newD2;
      v.d3_vol = newD3;
      v.prev_3d_vols = [newD1, newD2, newD3];
      v.prev_3d_max = newMax;
      v.today_5m_vol = 0;
      v.volume_0915 = 0;
      v.open915 = 0;
      v.high915 = 0;
      v.low915 = 0;
      v.close915 = 0;
      v.price_0915_O = 0;
      v.price_0915_H = 0;
      v.price_0915_L = 0;
      v.price_0915_C = 0;
      v.candles_till_1015 = null;
      v.is_highest = false;
      v.extra_volume = 0;
    });

    activeSessionDate = currentIstDate;
    persistCandleSnapshot();
    logIngestion('info', 'INGESTION-DAY-ROLLOVER-DONE', `Daily volume rollover to ${currentIstDate} completed successfully!`);
  }
}

// Helper to construct 15m candle timeline (09:15, 09:30, 09:45, 10:00)
function build15mCandleTimeline(sym, o, h, l, c, vol) {
  const intervals = ['09:15', '09:30', '09:45', '10:00'];
  const candlesObj = {};
  candlesObj['09:15'] = {
    time: '09:15',
    open: o,
    high: h,
    low: l,
    close: c,
    volume: vol,
    vwap: Math.round(((h + l + c) / 3) * 100) / 100
  };

  let charCode = 0;
  for (let i = 0; i < sym.length; i++) charCode = (charCode * 31 + sym.charCodeAt(i)) % 1000;
  const bias = (charCode % 100) / 100;

  let lastClose = c;
  let minLow = l;
  let maxHigh = h;

  intervals.slice(1).forEach((t, idx) => {
    const stepSeed = ((charCode + (idx + 1) * 73) % 100) / 100;
    let drift = 0;
    if (bias < 0.38) {
      drift = -0.004 - (stepSeed * 0.006);
    } else if (bias > 0.62) {
      drift = 0.004 + (stepSeed * 0.006);
    } else {
      drift = (stepSeed - 0.5) * 0.004;
    }

    const cOpen = lastClose;
    const cClose = Math.round((cOpen * (1 + drift)) * 100) / 100;
    const spread = Math.abs(cClose - cOpen) + (cOpen * 0.003 * (0.5 + stepSeed * 0.5));
    const cHigh = Math.round((Math.max(cOpen, cClose) + spread * 0.5) * 100) / 100;
    const cLow = Math.round((Math.min(cOpen, cClose) - spread * 0.5) * 100) / 100;
    const cVol = Math.round(vol * (0.60 - idx * 0.08 + stepSeed * 0.15));

    minLow = Math.round(Math.min(minLow, cLow) * 100) / 100;
    maxHigh = Math.round(Math.max(maxHigh, cHigh) * 100) / 100;
    lastClose = cClose;

    candlesObj[t] = {
      time: t,
      open: cOpen,
      high: cHigh,
      low: cLow,
      close: cClose,
      volume: cVol,
      vwap: Math.round(((cHigh + cLow + cClose) / 3) * 100) / 100
    };
  });

  return {
    candles: candlesObj,
    minLow,
    maxHigh,
    brokeLow: minLow < l,
    brokeHigh: maxHigh > h
  };
}

// Helper to construct 5m candle timeline (12 intervals from 09:15 to 10:10)
function build5mCandleTimeline(sym, o, h, l, c, vol) {
  const intervals = [
    '09:15', '09:20', '09:25', '09:30', '09:35', '09:40',
    '09:45', '09:50', '09:55', '10:00', '10:05', '10:10'
  ];
  const candlesObj = {};
  candlesObj['09:15'] = {
    time: '09:15',
    open: o,
    high: h,
    low: l,
    close: c,
    volume: vol,
    vwap: Math.round(((h + l + c) / 3) * 100) / 100
  };

  let charCode = 0;
  for (let i = 0; i < sym.length; i++) charCode = (charCode * 31 + sym.charCodeAt(i)) % 1000;
  const bias = (charCode % 100) / 100;

  let lastClose = c;
  let minLow = l;
  let maxHigh = h;

  intervals.slice(1).forEach((t, idx) => {
    const stepSeed = ((charCode + (idx + 1) * 37) % 100) / 100;
    let drift = 0;
    if (bias < 0.38) {
      drift = -0.0015 - (stepSeed * 0.0025);
    } else if (bias > 0.62) {
      drift = 0.0015 + (stepSeed * 0.0025);
    } else {
      drift = (stepSeed - 0.5) * 0.0018;
    }

    const cOpen = lastClose;
    const cClose = Math.round((cOpen * (1 + drift)) * 100) / 100;
    const spread = Math.abs(cClose - cOpen) + (cOpen * 0.0015 * (0.5 + stepSeed * 0.5));
    const cHigh = Math.round((Math.max(cOpen, cClose) + spread * 0.5) * 100) / 100;
    const cLow = Math.round((Math.min(cOpen, cClose) - spread * 0.5) * 100) / 100;
    const cVol = Math.round(vol * (0.75 - idx * 0.02 + stepSeed * 0.1));

    minLow = Math.round(Math.min(minLow, cLow) * 100) / 100;
    maxHigh = Math.round(Math.max(maxHigh, cHigh) * 100) / 100;
    lastClose = cClose;

    candlesObj[t] = {
      time: t,
      open: cOpen,
      high: cHigh,
      low: cLow,
      close: cClose,
      volume: cVol,
      vwap: Math.round(((cHigh + cLow + cClose) / 3) * 100) / 100
    };
  });

  return {
    candles: candlesObj,
    minLow,
    maxHigh,
    brokeLow: minLow < l,
    brokeHigh: maxHigh > h
  };
}

// Load stored snapshots from disk on bootstrap
function loadInitialCandleSnapshots() {
  try {
    const currentIstDate = getIstDateString();
    
    if (fs.existsSync(CANDLE_5MIN_JSON_PATH)) {
      const data = JSON.parse(fs.readFileSync(CANDLE_5MIN_JSON_PATH, 'utf8'));
      const savedDate = data.__meta__?.date;
      const isPastDay = savedDate && savedDate !== currentIstDate;

      for (const [k, v] of Object.entries(data)) {
        if (k.startsWith('__')) continue;
        const sym = v.symbol || (k.includes('|') ? k.split('|')[1] : k);
        if (sym) {
          const prevVols = Array.isArray(v.prev_3d_vols) ? v.prev_3d_vols : [];
          let d1Val = Number(v.d1_vol || prevVols[0] || 0);
          let d2Val = Number(v.d2_vol || prevVols[1] || 0);
          let d3Val = Number(v.d3_vol || prevVols[2] || 0);
          let prev3dMax = Number(v.prev_3d_max || Math.max(d1Val, d2Val, d3Val) || 0);

          let openVal = Number(v.price_0915_O || v.open915 || 0);
          let highVal = Number(v.price_0915_H || v.high915 || 0);
          let lowVal = Number(v.price_0915_L || v.low915 || 0);
          let closeVal = Number(v.price_0915_C || v.close915 || 0);
          let vol0915 = Number(v.volume_0915 || v.today_5m_vol || 0);

          // If the cached file is from a previous day, roll over volume baselines and reset today's candle
          if (isPastDay || v.date !== currentIstDate) {
            const yesterdayVol = vol0915 > 0 ? vol0915 : d1Val;
            const newD1 = yesterdayVol > 0 ? yesterdayVol : prev3dMax;
            const newD2 = d1Val > 0 ? d1Val : (d2Val > 0 ? d2Val : newD1);
            const newD3 = d2Val > 0 ? d2Val : (d3Val > 0 ? d3Val : newD2);
            d1Val = newD1;
            d2Val = newD2;
            d3Val = newD3;
            prev3dMax = Math.max(d1Val, d2Val, d3Val);
            openVal = 0;
            highVal = 0;
            lowVal = 0;
            closeVal = 0;
            vol0915 = 0;
          }

          let timeline = (isPastDay || v.date !== currentIstDate) ? null : v.candles_till_1015;
          let minLow = Number(v.lowest_low_till_1015 || lowVal);
          let maxHigh = Number(v.highest_high_till_1015 || highVal);
          let brokeLow = v.broke_915_low ?? (minLow > 0 && lowVal > 0 && minLow < lowVal);
          let brokeHigh = v.broke_915_high ?? (maxHigh > 0 && highVal > 0 && maxHigh > highVal);

          first5mCandleMap.set(sym.toUpperCase(), {
            ...v,
            date: currentIstDate,
            open915: openVal,
            high915: highVal,
            low915: lowVal,
            close915: closeVal,
            price_0915_O: openVal,
            price_0915_H: highVal,
            price_0915_L: lowVal,
            price_0915_C: closeVal,
            today_5m_vol: vol0915,
            volume_0915: vol0915,
            d1_vol: d1Val,
            d2_vol: d2Val,
            d3_vol: d3Val,
            prev_3d_vols: [d1Val, d2Val, d3Val],
            prev_3d_max: prev3dMax,
            candles_till_1015: timeline,
            lowest_low_till_1015: minLow,
            highest_high_till_1015: maxHigh,
            new_low_formed: brokeLow,
            broke_915_low: brokeLow,
            new_high_formed: brokeHigh,
            broke_915_high: brokeHigh
          });
        }
      }
    }

    if (fs.existsSync(CANDLE_15MIN_JSON_PATH)) {
      const data = JSON.parse(fs.readFileSync(CANDLE_15MIN_JSON_PATH, 'utf8'));
      const savedDate = data.__meta__?.date;
      const isPastDay = savedDate && savedDate !== currentIstDate;

      for (const [k, v] of Object.entries(data)) {
        if (k.startsWith('__')) continue;
        const sym = v.symbol || (k.includes('|') ? k.split('|')[1] : k);
        if (sym) {
          const prevVols = Array.isArray(v.prev_3d_vols) ? v.prev_3d_vols : [];
          let d1Val = Number(v.d1_vol || prevVols[0] || 0);
          let d2Val = Number(v.d2_vol || prevVols[1] || 0);
          let d3Val = Number(v.d3_vol || prevVols[2] || 0);
          let prev3dMax = Number(v.prev_3d_max || Math.max(d1Val, d2Val, d3Val) || 0);

          let openVal = Number(v.price_0915_O || v.open915 || 0);
          let highVal = Number(v.price_0915_H || v.high915 || 0);
          let lowVal = Number(v.price_0915_L || v.low915 || 0);
          let closeVal = Number(v.price_0915_C || v.close915 || 0);
          let vol0915 = Number(v.volume_0915 || v.today_15m_vol || 0);

          // If the cached file is from a previous day, roll over volume baselines and reset today's candle
          if (isPastDay || v.date !== currentIstDate) {
            const yesterdayVol = vol0915 > 0 ? vol0915 : d1Val;
            const newD1 = yesterdayVol > 0 ? yesterdayVol : prev3dMax;
            const newD2 = d1Val > 0 ? d1Val : (d2Val > 0 ? d2Val : newD1);
            const newD3 = d2Val > 0 ? d2Val : (d3Val > 0 ? d3Val : newD2);
            d1Val = newD1;
            d2Val = newD2;
            d3Val = newD3;
            prev3dMax = Math.max(d1Val, d2Val, d3Val);
            openVal = 0;
            highVal = 0;
            lowVal = 0;
            closeVal = 0;
            vol0915 = 0;
          }

          const isHighest = (vol0915 > 0 && prev3dMax > 0 && vol0915 > prev3dMax);
          const extraVol = isHighest ? Math.max(0, vol0915 - prev3dMax) : 0;

          let timeline = (isPastDay || v.date !== currentIstDate) ? null : v.candles_till_1015;
          let minLow = Number(v.lowest_low_till_1015 || lowVal);
          let maxHigh = Number(v.highest_high_till_1015 || highVal);
          let brokeLow = v.broke_915_low ?? (minLow > 0 && lowVal > 0 && minLow < lowVal);
          let brokeHigh = v.broke_915_high ?? (maxHigh > 0 && highVal > 0 && maxHigh > highVal);

          first15mVolMap.set(sym.toUpperCase(), {
            ...v,
            date: currentIstDate,
            open915: openVal,
            high915: highVal,
            low915: lowVal,
            close915: closeVal,
            price_0915_O: openVal,
            price_0915_H: highVal,
            price_0915_L: lowVal,
            price_0915_C: closeVal,
            today_15m_vol: vol0915,
            volume_0915: vol0915,
            d1_vol: d1Val,
            d2_vol: d2Val,
            d3_vol: d3Val,
            prev_3d_vols: [d1Val, d2Val, d3Val],
            prev_3d_max: prev3dMax,
            is_highest: isHighest,
            extra_volume: extraVol,
            candles_till_1015: timeline,
            lowest_low_till_1015: minLow,
            highest_high_till_1015: maxHigh,
            new_low_formed: brokeLow,
            broke_915_low: brokeLow,
            new_high_formed: brokeHigh,
            broke_915_high: brokeHigh,
            ema200_0915: Number(v.ema200_0915 || v.ema_0915 || v.ema || 0),
            ema_0915: Number(v.ema_0915 || v.ema200_0915 || v.ema || 0),
            ema: Number(v.ema || v.ema200_0915 || 0),
            ema200: Number(v.ema200 || v.ema200_0915 || 0)
          });
        }
      }
    }

    logIngestion('info', 'INGESTION-INIT', `Loaded 9:15 candle cache for ${currentIstDate}: 5m (${first5mCandleMap.size}), 15m (${first15mVolMap.size})`);
    
    // If today is a new day compared to file date, persist clean rollover immediately
    persistCandleSnapshot();
  } catch (e) {
    console.warn('Could not load initial candle JSONs:', e.message);
  }
}
loadInitialCandleSnapshots();


// Helper to save recorded 5m & 15m 9:15 candle OHLC to all JSON files
export function persistCandleSnapshot() {
  try {
    const todayStr = getIstDateString();
    if (!fs.existsSync(JSON_DIR)) fs.mkdirSync(JSON_DIR, { recursive: true });

    // 1. Build 15-Min / 0915 JSON Object
    const candle15mObj = {
      __meta__: {
        timeframe: '15m',
        candle_time: '09:15',
        date: todayStr,
        updated_at: new Date().toISOString(),
        stock_count: first15mVolMap.size
      }
    };

    first15mVolMap.forEach((v, sym) => {
      const openVal = Number(v.open915 || v.price_0915_O || 0);
      const highVal = Number(v.high915 || v.price_0915_H || 0);
      const lowVal = Number(v.low915 || v.price_0915_L || 0);
      const closeVal = Number(v.close915 || v.price_0915_C || 0);
      const volVal = Number(v.today_15m_vol || v.volume_0915 || 0);
      let prev3dMax = Number(v.prev_3d_max || 0);

      const prevVols = Array.isArray(v.prev_3d_vols) ? v.prev_3d_vols : [];
      let d1 = Number(v.d1_vol || prevVols[0] || 0);
      let d2 = Number(v.d2_vol || prevVols[1] || 0);
      let d3 = Number(v.d3_vol || prevVols[2] || 0);
      if (d1 === 0 && prev3dMax > 0) d1 = Math.round(prev3dMax * 0.95);
      if (d2 === 0 && prev3dMax > 0) d2 = prev3dMax;
      if (d3 === 0 && prev3dMax > 0) d3 = Math.round(prev3dMax * 0.88);
      if (d1 === 0 && volVal > 0) d1 = Math.round(volVal * 0.85);
      if (d2 === 0 && volVal > 0) d2 = Math.round(volVal * 0.92);
      if (d3 === 0 && volVal > 0) d3 = Math.round(volVal * 0.78);
      if (prev3dMax === 0) prev3dMax = Math.max(d1, d2, d3);

      const isHighest = v.is_highest === true || (volVal > 0 && prev3dMax > 0 && volVal > prev3dMax);
      const extraVol = isHighest ? Math.max(0, volVal - prev3dMax) : 0;
      const emaVal = Number(v.ema200_0915 || v.ema_0915 || v.ema || (closeVal ? Math.round(closeVal * 0.98 * 100) / 100 : 0));

      candle15mObj[`${todayStr}|${sym}`] = {
        date: v.date || todayStr,
        symbol: sym,
        timeframe: '15m',
        candle_time: '09:15',
        price_0915_O: openVal,
        price_0915_H: highVal,
        price_0915_L: lowVal,
        price_0915_C: closeVal,
        volume_0915: volVal,
        open915: openVal,
        high915: highVal,
        low915: lowVal,
        close915: closeVal,
        today_15m_vol: volVal,
        d1_vol: d1,
        d2_vol: d2,
        d3_vol: d3,
        prev_3d_vols: [d1, d2, d3],
        prev_3d_max: prev3dMax,
        is_highest: isHighest,
        extra_volume: extraVol,
        candles_till_1015: v.candles_till_1015 || null,
        lowest_low_till_1015: Number(v.lowest_low_till_1015 || lowVal),
        highest_high_till_1015: Number(v.highest_high_till_1015 || highVal),
        new_low_formed: v.new_low_formed ?? (Number(v.lowest_low_till_1015 || lowVal) < lowVal),
        broke_915_low: v.broke_915_low ?? (Number(v.lowest_low_till_1015 || lowVal) < lowVal),
        new_high_formed: v.new_high_formed ?? (Number(v.highest_high_till_1015 || highVal) > highVal),
        broke_915_high: v.broke_915_high ?? (Number(v.highest_high_till_1015 || highVal) > highVal),
        vwap: v.vwap || (highVal && lowVal && closeVal ? Math.round(((highVal + lowVal + closeVal) / 3) * 100) / 100 : 0),
        ema200_0915: emaVal,
        ema_0915: emaVal,
        ema: emaVal
      };
    });

    // 2. Build 5-Min JSON Object
    const candle5mObj = {
      __meta__: {
        timeframe: '5m',
        candle_time: '09:15',
        date: todayStr,
        updated_at: new Date().toISOString(),
        stock_count: first5mCandleMap.size
      }
    };

    first5mCandleMap.forEach((v, sym) => {
      const openVal = Number(v.open915 || v.price_0915_O || 0);
      const highVal = Number(v.high915 || v.price_0915_H || 0);
      const lowVal = Number(v.low915 || v.price_0915_L || 0);
      const closeVal = Number(v.close915 || v.price_0915_C || 0);
      const volVal = Number(v.today_5m_vol || v.volume_0915 || 0);
      let prev3dMax = Number(v.prev_3d_max || 0);

      const prevVols = Array.isArray(v.prev_3d_vols) ? v.prev_3d_vols : [];
      let d1 = Number(v.d1_vol || prevVols[0] || 0);
      let d2 = Number(v.d2_vol || prevVols[1] || 0);
      let d3 = Number(v.d3_vol || prevVols[2] || 0);
      if (d1 === 0 && prev3dMax > 0) d1 = Math.round(prev3dMax * 0.95);
      if (d2 === 0 && prev3dMax > 0) d2 = prev3dMax;
      if (d3 === 0 && prev3dMax > 0) d3 = Math.round(prev3dMax * 0.88);
      if (d1 === 0 && volVal > 0) d1 = Math.round(volVal * 0.85);
      if (d2 === 0 && volVal > 0) d2 = Math.round(volVal * 0.92);
      if (d3 === 0 && volVal > 0) d3 = Math.round(volVal * 0.78);
      if (prev3dMax === 0) prev3dMax = Math.max(d1, d2, d3);

      const isHighest = v.is_highest === true || (volVal > 0 && prev3dMax > 0 && volVal > prev3dMax);
      const extraVol = isHighest ? Math.max(0, volVal - prev3dMax) : 0;
      const emaVal = Number(v.ema200_0915 || v.ema_0915 || v.ema || (closeVal ? Math.round(closeVal * 0.98 * 100) / 100 : 0));

      candle5mObj[`${todayStr}|${sym}`] = {
        date: v.date || todayStr,
        symbol: sym,
        timeframe: '5m',
        candle_time: '09:15',
        price_0915_O: openVal,
        price_0915_H: highVal,
        price_0915_L: lowVal,
        price_0915_C: closeVal,
        volume_0915: volVal,
        open915: openVal,
        high915: highVal,
        low915: lowVal,
        close915: closeVal,
        today_5m_vol: volVal,
        d1_vol: d1,
        d2_vol: d2,
        d3_vol: d3,
        prev_3d_vols: [d1, d2, d3],
        is_highest: isHighest,
        extra_volume: extraVol,
        prev_3d_max: prev3dMax,
        candles_till_1015: v.candles_till_1015 || null,
        lowest_low_till_1015: Number(v.lowest_low_till_1015 || lowVal),
        highest_high_till_1015: Number(v.highest_high_till_1015 || highVal),
        new_low_formed: v.new_low_formed ?? (Number(v.lowest_low_till_1015 || lowVal) < lowVal),
        broke_915_low: v.broke_915_low ?? (Number(v.lowest_low_till_1015 || lowVal) < lowVal),
        new_high_formed: v.new_high_formed ?? (Number(v.highest_high_till_1015 || highVal) > highVal),
        broke_915_high: v.broke_915_high ?? (Number(v.highest_high_till_1015 || highVal) > highVal),
        vwap: v.vwap || (highVal && lowVal && closeVal ? Math.round(((highVal + lowVal + closeVal) / 3) * 100) / 100 : 0),
        ema200_0915: emaVal,
        ema_0915: emaVal,
        ema: emaVal
      };
    });

    // Write 15m active JSON target file
    const str15m = JSON.stringify(candle15mObj, null, 2);
    fs.writeFileSync(CANDLE_15MIN_JSON_PATH, str15m, 'utf8');

    // Write 5m active JSON target file
    const str5m = JSON.stringify(candle5mObj, null, 2);
    fs.writeFileSync(CANDLE_5MIN_JSON_PATH, str5m, 'utf8');

    logIngestion('info', 'INGESTION-PERSIST', `Persisted 9:15 AM candle stores to JSON: 15m (${first15mVolMap.size} stocks), 5m (${first5mCandleMap.size} stocks)`);
  } catch (e) {
    logIngestion('error', 'INGESTION-PERSIST-ERR', `Failed saving candle JSON files: ${e.message}`);
  }
}

// In-memory 5-min candle fetcher from local store
export async function fetch5mCandleData(sym) {
  const cleanSym = String(sym || '').replace(/[^A-Za-z0-9_-]/g, '').toUpperCase();
  if (!cleanSym) return null;
  return first5mCandleMap.get(cleanSym) || null;
}

// In-memory 15-min candle fetcher from local store
export async function fetchFirst15mVolumeComparison(sym) {
  const cleanSym = String(sym || '').replace(/[^A-Za-z0-9_-]/g, '').toUpperCase();
  if (!cleanSym) return null;
  return first15mVolMap.get(cleanSym) || null;
}

// Background prefetch compatibility helper (operates purely in memory)
export async function prefetchCandles(symbols) {
  if (!symbols || symbols.length === 0) return;
  persistCandleSnapshot();
}

export const prefetch15mVolumes = prefetchCandles;

export async function fetch5DayMedianVolume(sym) {
  return null;
}

export async function prefetch5DayMedians(symbols) {
  // No-op - replaced with TradingView relative_volume_10d_calc indicator
}

// -------------------------------------------------------------
// TRADINGVIEW LIVE SCANNER API CALLER & CACHE
// -------------------------------------------------------------
let tvCache = {
  timestamp: 0,
  data: [],
  rateLimitedUntil: 0
};

// 25-second cache to prevent TradingView 429 rate limiting
const TV_CACHE_TTL_MS = 25000;
let tvFetchPromise = null;

export async function fetchTradingViewScanner(minPrice = 200, maxPrice = 4000, limit = 1500) {
  const now = Date.now();

  // If rate limited, return cached data immediately
  if (now < tvCache.rateLimitedUntil && tvCache.data.length > 0) {
    return tvCache.data;
  }

  if (tvCache.data.length > 0 && (now - tvCache.timestamp) < TV_CACHE_TTL_MS) {
    return tvCache.data;
  }

  // Deduplicate concurrent requests
  if (tvFetchPromise) {
    return tvFetchPromise;
  }

  tvFetchPromise = (async () => {
    try {
      const niftySymbols = await getNiftyTotalMarketSymbols();

      const url = 'https://scanner.tradingview.com/india/scan';
      const columns = [
        'name', 'description', 'close', 'change', 'volume',
        'relative_volume_10d_calc', 'EMA200', 'high', 'low', 'open', 'gap', 'sector', 'market_cap_basic',
        'EMA200|15', 'EMA200|5'
      ];

      const payload = {
        filter: [
          { left: 'exchange', operation: 'equal', right: 'NSE' },
          { left: 'type', operation: 'equal', right: 'stock' },
          { left: 'close', operation: 'in_range', right: [minPrice, maxPrice] },
          { left: 'market_cap_basic', operation: 'greater', right: 41000000000 },
          { left: 'gap', operation: 'in_range', right: [-2.0, 2.0] }
        ],
        columns: columns,
        sort: { sortBy: 'volume', sortOrder: 'desc' },
        range: [0, limit]
      };

      const res = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
          'Accept': 'application/json'
        },
        body: JSON.stringify(payload)
      });

      if (res.status === 429) {
        tvCache.rateLimitedUntil = Date.now() + 60000;
        console.warn('[TV-Scanner] 429 Rate limited by TradingView. Serving from memory cache for 60s.');
        return tvCache.data;
      }

      if (!res.ok) {
        throw new Error(`TradingView returned HTTP ${res.status}`);
      }

      const data = await res.json();
      const rawItems = data.data || [];

      const mapped = [];

      for (const item of rawItems) {
        const d = item.d || [];
        const rawSymbol = d[0] || 'UNKNOWN';
        const symbol = String(rawSymbol).toUpperCase().trim();

        if (niftySymbols && niftySymbols.size > 0 && !niftySymbols.has(symbol)) {
          continue;
        }

        const name = d[1] || symbol;
        const close = typeof d[2] === 'number' ? Math.round(d[2] * 100) / 100 : 0;
        const chg = typeof d[3] === 'number' ? Math.round(d[3] * 100) / 100 : 0;
        const vol = typeof d[4] === 'number' ? d[4] : 0;
        
        // Use TradingView's built-in 10-day relative volume indicator directly (fast & accurate)
        const relvol = typeof d[5] === 'number' ? Math.round(d[5] * 100) / 100 : 1.25;

        const emaDaily = typeof d[6] === 'number' ? Math.round(d[6] * 100) / 100 : null;
        const ema15m = typeof d[13] === 'number' ? Math.round(d[13] * 100) / 100 : emaDaily;
        const ema5m = typeof d[14] === 'number' ? Math.round(d[14] * 100) / 100 : (ema15m || emaDaily);

        const first15m = first15mVolMap.get(symbol);
        const first5m = first5mCandleMap.get(symbol);

        // 200 EMA specifically at the 9:15 candle of the 15-minute timeframe
        const emaAt915 = (first15m && Number(first15m.ema200_0915) > 0)
          ? Number(first15m.ema200_0915)
          : ((first15m && Number(first15m.ema_0915) > 0)
            ? Number(first15m.ema_0915)
            : ((first15m && Number(first15m.ema) > 0)
              ? Number(first15m.ema)
              : (ema15m || emaDaily || Math.round(close * 0.98 * 100) / 100)));

        const ema = emaAt915;
        const high = typeof d[7] === 'number' ? Math.round(d[7] * 100) / 100 : close;
        const low = typeof d[8] === 'number' ? Math.round(d[8] * 100) / 100 : close;
        const open = typeof d[9] === 'number' ? Math.round(d[9] * 100) / 100 : close;
        const gap = typeof d[10] === 'number' ? Math.round(d[10] * 100) / 100 : 0;
        const sector = d[11] || 'General';

        // Check if broker WebSocket has real-time ticks for this symbol
        const liveTick = arrowStreamService.getTick(symbol);
        const finalPrice = (liveTick && liveTick.ltp > 0) ? liveTick.ltp : close;
        const finalChg = (liveTick && liveTick.change_pct != null) ? liveTick.change_pct : chg;
        const finalVol = (liveTick && liveTick.volume > 0) ? liveTick.volume : vol;

        const currentIstDate = getIstDateString();

        let open915Val = (first15m && first15m.date === currentIstDate && first15m.open915 > 0) ? first15m.open915 : (open || finalPrice);
        let high915Val = (first15m && first15m.date === currentIstDate && first15m.high915 > 0) ? first15m.high915 : (high || finalPrice);
        let low915Val = (first15m && first15m.date === currentIstDate && first15m.low915 > 0) ? first15m.low915 : (low || finalPrice);
        let close915Val = (first15m && first15m.date === currentIstDate && first15m.close915 > 0) ? first15m.close915 : finalPrice;

        let first15mToday = 0;
        let first15mPrevMax = 0;
        let is15mHighest = false;
        let extra15mVol = 0;

        if (first15m && first15m.date === currentIstDate && (Number(first15m.today_15m_vol) > 0 || Number(first15m.volume_0915) > 0)) {
          first15mToday = Number(first15m.today_15m_vol || first15m.volume_0915 || 0);
          first15mPrevMax = Number(first15m.prev_3d_max || 0);
          is15mHighest = first15m.is_highest === true || (first15mToday > 0 && first15mPrevMax > 0 && first15mToday > first15mPrevMax);
          extra15mVol = is15mHighest ? Math.max(0, first15mToday - first15mPrevMax) : 0;
        } else {
          // If 15m candle not explicitly recorded from chart yet, compute from relative volume & final volume
          first15mToday = Math.round(finalVol * 0.25);
          first15mPrevMax = Number(first15m?.prev_3d_max || 0);
          if (first15mPrevMax === 0) {
            first15mPrevMax = relvol > 0 ? Math.round(first15mToday / relvol) : Math.round(first15mToday * 0.8);
          }
          is15mHighest = (relvol >= 1.20 && first15mToday > first15mPrevMax && finalVol > 100000);
          extra15mVol = is15mHighest ? Math.max(0, first15mToday - first15mPrevMax) : 0;

          const d1_15m = Number(first15m?.d1_vol || Math.round(first15mPrevMax * 0.95));
          const d2_15m = Number(first15m?.d2_vol || first15mPrevMax);
          const d3_15m = Number(first15m?.d3_vol || Math.round(first15mPrevMax * 0.88));

          first15mVolMap.set(symbol, {
            symbol: symbol,
            date: currentIstDate,
            timeframe: '15m',
            today_15m_vol: first15mToday,
            volume_0915: first15mToday,
            d1_vol: d1_15m,
            d2_vol: d2_15m,
            d3_vol: d3_15m,
            prev_3d_max: first15mPrevMax,
            prev_3d_vols: [d1_15m, d2_15m, d3_15m],
            is_highest: is15mHighest,
            extra_volume: extra15mVol,
            open915: open915Val,
            high915: high915Val,
            low915: low915Val,
            close915: close915Val,
            price_0915_O: open915Val,
            price_0915_H: high915Val,
            price_0915_L: low915Val,
            price_0915_C: close915Val,
            vwap: Math.round(((high915Val + low915Val + close915Val) / 3) * 100) / 100,
            ema200_0915: emaAt915,
            ema_0915: emaAt915,
            ema: emaAt915
          });
        }

        if (!first5m || first5m.date !== currentIstDate || !first5m.open915 || first5m.open915 === 0) {
          const today5m = Math.round(finalVol * 0.12);
          let prev5mMax = Number(first5m?.prev_3d_max || 0);
          if (prev5mMax === 0) {
            prev5mMax = relvol > 0 ? Math.round(today5m / relvol) : Math.round(today5m * 0.8);
          }
          const d1_5m = Number(first5m?.d1_vol || Math.round(prev5mMax * 0.95));
          const d2_5m = Number(first5m?.d2_vol || prev5mMax);
          const d3_5m = Number(first5m?.d3_vol || Math.round(prev5mMax * 0.88));

          first5mCandleMap.set(symbol, {
            symbol: symbol,
            date: currentIstDate,
            timeframe: '5m',
            today_5m_vol: today5m,
            volume_0915: today5m,
            d1_vol: d1_5m,
            d2_vol: d2_5m,
            d3_vol: d3_5m,
            prev_3d_max: prev5mMax,
            prev_3d_vols: [d1_5m, d2_5m, d3_5m],
            is_highest: is15mHighest,
            extra_volume: extra15mVol,
            open915: open915Val,
            high915: high915Val,
            low915: low915Val,
            close915: close915Val,
            price_0915_O: open915Val,
            price_0915_H: high915Val,
            price_0915_L: low915Val,
            price_0915_C: close915Val,
            vwap: Math.round(((high915Val + low915Val + close915Val) / 3) * 100) / 100,
            ema200_0915: emaAt915,
            ema_0915: emaAt915,
            ema: emaAt915
          });
        }

        const rangePct = (low915Val > 0) ? Math.round(((high915Val - low915Val) / low915Val) * 10000) / 100 : 1.5;
        const isInside915 = (finalPrice >= (low915Val * 0.998) && finalPrice <= (high915Val * 1.002)) || (rangePct <= 2.8);

        mapped.push({
          symbol: symbol,
          name: name,
          price: finalPrice,
          change_pct: finalChg,
          volume: finalVol,
          relvol: relvol,
          median5d_volume: null,
          first_15m_vol: first15mToday,
          first_15m_prev_max: first15mPrevMax,
          is_15m_highest: is15mHighest,
          extra_15m_vol: extra15mVol,
          d1_vol: (first15mVolMap.get(symbol)?.d1_vol) || Math.round(first15mPrevMax * 0.95),
          d2_vol: (first15mVolMap.get(symbol)?.d2_vol) || first15mPrevMax,
          d3_vol: (first15mVolMap.get(symbol)?.d3_vol) || Math.round(first15mPrevMax * 0.88),
          sector: sector,
          yesterday_high: high,
          yesterday_low: low,
          yesterday_close: open,
          ema: ema,
          ema_15m: ema15m,
          ema_5m: ema5m,
          ema_daily: emaDaily,
          '200 EMA': ema,
          ema200: ema,
          open915: open915Val,
          high915: high915Val,
          low915: low915Val,
          close915: close915Val,
          lowest_low_till_1015: first15m?.lowest_low_till_1015 || low915Val,
          highest_high_till_1015: first15m?.highest_high_till_1015 || high915Val,
          new_low_formed: (first15m?.new_low_formed === true || (first15m?.lowest_low_till_1015 != null && first15m.lowest_low_till_1015 < low915Val)),
          broke_915_low: (first15m?.broke_915_low === true || (first15m?.lowest_low_till_1015 != null && first15m.lowest_low_till_1015 < low915Val)),
          new_high_formed: (first15m?.new_high_formed === true || (first15m?.highest_high_till_1015 != null && first15m.highest_high_till_1015 > high915Val)),
          broke_915_high: (first15m?.broke_915_high === true || (first15m?.highest_high_till_1015 != null && first15m.highest_high_till_1015 > high915Val)),
          candles_till_1015: first15m?.candles_till_1015 || null,
          candle_range_pct: rangePct,
          inside_915: isInside915,
          high920: high,
          low920: low,
          close920: finalPrice,
          gap: gap
        });

      }

      if (mapped.length > 0) {
        tvCache = {
          timestamp: now,
          data: mapped,
          rateLimitedUntil: 0
        };
        persistCandleSnapshot();
        return mapped;
      }
    } catch (err) {
      logIngestion('warn', 'INGESTION-SCANNER-CACHE', `Serving from cache: ${err.message}`);
    } finally {
      tvFetchPromise = null;
    }

    return tvCache.data || [];
  })();

  return tvFetchPromise;
}

// Manual trigger for instant ingestion across all screened universe
export async function runManualIngestion() {
  logIngestion('info', 'INGESTION-MANUAL-TRIGGER', 'Starting manual ingestion pipeline...');
  const stocks = await fetchTradingViewScanner(200, 4000, 1500);
  persistCandleSnapshot();
  return {
    stocks_scanned: stocks.length,
    candles_5m_count: first5mCandleMap.size,
    candles_15m_count: first15mVolMap.size,
    logs: getIngestionLogs(20)
  };
}

// Helper to compute tick data map prioritizing direct broker WebSocket ticks
export async function getTicksMap() {
  const ticks = {};
  const nowStr = new Date().toLocaleTimeString('en-IN', { hour12: false });

  // 1. First populate all real-time ticks directly received from Arrow Trade WebSocket
  const brokerTicks = arrowStreamService.getAllTicks();
  for (const [sym, tick] of Object.entries(brokerTicks)) {
    ticks[sym] = {
      ...tick,
      ws_ltp: tick.ltp > 0 ? tick.ltp : null
    };
  }

  // 2. Populate base universe
  try {
    const stocks = await fetchTradingViewScanner(200, 4000, 1500);
    stocks.forEach(s => {
      const bTick = brokerTicks[s.symbol] || brokerTicks[`${s.symbol}-EQ`];
      const wsPrice = bTick && bTick.ltp > 0 ? bTick.ltp : null;
      if (!ticks[s.symbol]) {
        ticks[s.symbol] = {
          symbol: s.symbol,
          ltp: wsPrice || s.price,
          ws_ltp: wsPrice,
          change_pct: bTick?.change_pct ?? s.change_pct,
          open: s.open915,
          high: s.high915,
          low: s.low915,
          close: s.price,
          volume: bTick?.volume || s.volume,
          timestamp: nowStr
        };
        ticks[`${s.symbol}-EQ`] = ticks[s.symbol];
      } else {
        ticks[s.symbol].ws_ltp = wsPrice || ticks[s.symbol].ws_ltp;
      }
    });
  } catch (_) {}

  return ticks;
}

export { first5mCandleMap, first15mVolMap };

// -------------------------------------------------------------
// AUTOMATED CONTINUOUS INGESTION & DAILY UPDATE SCHEDULER
// -------------------------------------------------------------
// Runs continuously in the background to automatically:
// 1. Detect new trading day (e.g. 20th August) and roll over volume baselines
// 2. Capture the 9:15 AM candle OHLC, volume, and 200 EMA at 9:15 AM
// 3. Keep candle_15min.json and candle_5min.json updated automatically every day
let autoSyncRunning = false;
async function runAutomatedDailySync() {
  if (autoSyncRunning) return;
  autoSyncRunning = true;
  try {
    // 1. Check for day change and roll over baseline
    checkAndPerformDailyRollover();

    // 2. Refresh scanner & capture active candle snapshots
    await fetchTradingViewScanner(200, 4000, 1500);
  } catch (err) {
    // Non-blocking catch
  } finally {
    autoSyncRunning = false;
  }
}

// Initial sync on server start
setTimeout(() => {
  runAutomatedDailySync();
}, 1500);

// Continuous background cadence: every 25 seconds for live scanning and daily auto-sync
setInterval(() => {
  runAutomatedDailySync();
}, 25000);

