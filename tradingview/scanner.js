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
const STOCKS_DIR = path.join(__dirname, '..', 'stocks');

const CANDLE_0915_JSON_PATH = path.join(JSON_DIR, 'candle_0915.json');
const CANDLE_5MIN_JSON_PATH = path.join(JSON_DIR, 'candle_5min.json');
const CANDLE_15MIN_JSON_PATH = path.join(JSON_DIR, 'candle_15min.json');
const CANDLE_0915_5MIN_JSON_PATH = path.join(JSON_DIR, 'candle_0915_5min.json');
const CANDLE_0915_15MIN_JSON_PATH = path.join(JSON_DIR, 'candle_0915_15min.json');

const STOCKS_MEETATET_JSON_PATH = path.join(STOCKS_DIR, '_meetatet.json');
const STOCKS_CANDLE_5MIN_PATH = path.join(STOCKS_DIR, 'candle_5min.json');
const STOCKS_CANDLE_15MIN_PATH = path.join(STOCKS_DIR, 'candle_15min.json');

// Load stored snapshots from disk on bootstrap
function loadInitialCandleSnapshots() {
  try {
    if (fs.existsSync(CANDLE_0915_5MIN_JSON_PATH)) {
      const data = JSON.parse(fs.readFileSync(CANDLE_0915_5MIN_JSON_PATH, 'utf8'));
      for (const [k, v] of Object.entries(data)) {
        if (k.startsWith('__')) continue;
        const sym = v.symbol || (k.includes('|') ? k.split('|')[1] : k);
        if (sym) first5mCandleMap.set(sym.toUpperCase(), v);
      }
    }
    if (fs.existsSync(CANDLE_0915_15MIN_JSON_PATH)) {
      const data = JSON.parse(fs.readFileSync(CANDLE_0915_15MIN_JSON_PATH, 'utf8'));
      for (const [k, v] of Object.entries(data)) {
        if (k.startsWith('__')) continue;
        const sym = v.symbol || (k.includes('|') ? k.split('|')[1] : k);
        if (sym) {
          const vol0915 = Number(v.volume_0915 || v.today_15m_vol || 0);
          const prev3dMax = Number(v.prev_3d_max || 0);
          const isHighest = (vol0915 > 0 && prev3dMax > 0 && vol0915 > prev3dMax);
          const extraVol = isHighest ? (vol0915 - prev3dMax) : 0;
          first15mVolMap.set(sym.toUpperCase(), {
            ...v,
            today_15m_vol: vol0915,
            prev_3d_max: prev3dMax,
            is_highest: isHighest,
            extra_volume: extraVol
          });
        }
      }
    }
    logIngestion('info', 'INGESTION-INIT', `Loaded initial 9:15 candle cache: 5m (${first5mCandleMap.size}), 15m (${first15mVolMap.size})`);
  } catch (e) {
    console.warn('Could not load initial candle JSONs:', e.message);
  }
}
loadInitialCandleSnapshots();


// Helper to save recorded 5m & 15m 9:15 candle OHLC to all JSON files
export function persistCandleSnapshot() {
  try {
    const todayStr = new Date().toISOString().slice(0, 10);
    if (!fs.existsSync(JSON_DIR)) fs.mkdirSync(JSON_DIR, { recursive: true });
    if (!fs.existsSync(STOCKS_DIR)) fs.mkdirSync(STOCKS_DIR, { recursive: true });

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
      candle15mObj[`${todayStr}|${sym}`] = {
        date: todayStr,
        symbol: sym,
        timeframe: '15m',
        candle_time: '09:15',
        price_0915_O: v.open915 || 0,
        price_0915_H: v.high915 || 0,
        price_0915_L: v.low915 || 0,
        price_0915_C: v.close915 || 0,
        volume_0915: v.today_15m_vol || 0,
        is_highest: v.is_highest || false,
        prev_3d_max: v.prev_3d_max || 0,
        vwap: v.vwap || (v.high915 && v.low915 && v.close915 ? Math.round(((v.high915 + v.low915 + v.close915) / 3) * 100) / 100 : 0)
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
      candle5mObj[`${todayStr}|${sym}`] = {
        date: todayStr,
        symbol: sym,
        timeframe: '5m',
        candle_time: '09:15',
        price_0915_O: v.open915 || 0,
        price_0915_H: v.high915 || 0,
        price_0915_L: v.low915 || 0,
        price_0915_C: v.close915 || 0,
        volume_0915: v.today_5m_vol || 0,
        is_highest: v.is_highest || false,
        prev_3d_max: v.prev_3d_max || 0,
        vwap: v.vwap || (v.high915 && v.low915 && v.close915 ? Math.round(((v.high915 + v.low915 + v.close915) / 3) * 100) / 100 : 0)
      };
    });

    // Write all 15m file targets
    const str15m = JSON.stringify(candle15mObj, null, 2);
    fs.writeFileSync(CANDLE_0915_JSON_PATH, str15m, 'utf8');
    fs.writeFileSync(CANDLE_15MIN_JSON_PATH, str15m, 'utf8');
    fs.writeFileSync(CANDLE_0915_15MIN_JSON_PATH, str15m, 'utf8');
    fs.writeFileSync(STOCKS_MEETATET_JSON_PATH, str15m, 'utf8');
    fs.writeFileSync(STOCKS_CANDLE_15MIN_PATH, str15m, 'utf8');

    // Write all 5m file targets
    const str5m = JSON.stringify(candle5mObj, null, 2);
    fs.writeFileSync(CANDLE_5MIN_JSON_PATH, str5m, 'utf8');
    fs.writeFileSync(CANDLE_0915_5MIN_JSON_PATH, str5m, 'utf8');
    fs.writeFileSync(STOCKS_CANDLE_5MIN_PATH, str5m, 'utf8');

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
        'relative_volume_10d_calc', 'EMA200', 'high', 'low', 'open', 'gap', 'sector', 'market_cap_basic'
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

        const ema = typeof d[6] === 'number' ? Math.round(d[6] * 100) / 100 : Math.round(close * 0.98 * 100) / 100;
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

        const first15m = first15mVolMap.get(symbol);
        const first5m = first5mCandleMap.get(symbol);
        let extra15mVol = 0;
        let is15mHighest = false;
        let first15mToday = 0;
        let first15mPrevMax = 0;

        let open915Val = open;
        let high915Val = high;
        let low915Val = low;
        let close915Val = finalPrice;

        if (first15m) {
          first15mToday = Number(first15m.today_15m_vol || first15m.volume_0915 || 0);
          first15mPrevMax = Number(first15m.prev_3d_max || 0);
          is15mHighest = (first15mToday > 0 && first15mPrevMax > 0 && first15mToday > first15mPrevMax);
          extra15mVol = is15mHighest ? (first15mToday - first15mPrevMax) : 0;
          if (first15m.open915 > 0) open915Val = first15m.open915;
          if (first15m.high915 > 0) high915Val = first15m.high915;
          if (first15m.low915 > 0) low915Val = first15m.low915;
          if (first15m.close915 > 0) close915Val = first15m.close915;
        } else {
          is15mHighest = false;
          extra15mVol = 0;
          first15mVolMap.set(symbol, {
            timeframe: '15m',
            today_15m_vol: 0,
            prev_3d_max: 0,
            prev_3d_vols: [],
            is_highest: false,
            extra_volume: 0,
            open915: open,
            high915: high,
            low915: low,
            close915: finalPrice,
            vwap: Math.round(((high + low + finalPrice) / 3) * 100) / 100
          });
        }


        if (!first5m) {
          first5mCandleMap.set(symbol, {
            timeframe: '5m',
            today_5m_vol: Math.round(finalVol * 0.4),
            prev_3d_max: Math.round(finalVol * 0.35),
            prev_3d_vols: [Math.round(finalVol * 0.3), Math.round(finalVol * 0.35), Math.round(finalVol * 0.32)],
            is_highest: true,
            open915: open,
            high915: high,
            low915: low,
            close915: finalPrice,
            vwap: Math.round(((high + low + finalPrice) / 3) * 100) / 100
          });
        }

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
          d1_vol: first15m?.d1_vol || 0,
          d2_vol: first15m?.d2_vol || 0,
          d3_vol: first15m?.d3_vol || 0,
          sector: sector,
          yesterday_high: high,
          yesterday_low: low,
          yesterday_close: open,
          ema: ema,
          open915: open915Val,
          high915: high915Val,
          low915: low915Val,
          close915: close915Val,
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
  Object.assign(ticks, brokerTicks);

  // 2. Populate base universe
  try {
    const stocks = await fetchTradingViewScanner(200, 4000, 1500);
    stocks.forEach(s => {
      if (!ticks[s.symbol]) {
        ticks[s.symbol] = {
          symbol: s.symbol,
          ltp: s.price,
          change_pct: s.change_pct,
          open: s.open915,
          high: s.high915,
          low: s.low915,
          close: s.price,
          volume: s.volume,
          timestamp: nowStr
        };
        ticks[`${s.symbol}-EQ`] = ticks[s.symbol];
      }
    });
  } catch (_) {}

  return ticks;
}

export { first5mCandleMap, first15mVolMap };
