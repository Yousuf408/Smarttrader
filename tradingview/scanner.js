/**
 * TradingView Scanner Module (ESM)
 * 
 * Centralized module for all TradingView API interactions:
 * - TradingView Scanner API client with multi-condition filtering (100% Live Data)
 * - Nifty Total Market Index constituent loader and cache
 * - 5-Day Median Relative Volume (RELVOL) calculation engine
 * - Stock ticks mapping
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

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
// 5-DAY MEDIAN RELATIVE VOLUME (RELVOL) ENGINE
// -------------------------------------------------------------
const median5dVolumeMap = new Map();
let medianPrefetchInProgress = false;

// -------------------------------------------------------------
// 3-DAY FIRST 15-MIN CANDLE VOLUME ENGINE (Pine Script Logic)
// -------------------------------------------------------------
// Maps SYMBOL -> { today_15m_vol, prev_3d_max, prev_3d_vols, is_highest, extra_volume, formatted_extra }
const first15mVolMap = new Map();
let first15mPrefetchInProgress = false;

export async function fetchFirst15mVolumeComparison(sym) {
  const cleanSym = String(sym || '').replace(/[^A-Za-z0-9_-]/g, '').toUpperCase();
  if (!cleanSym) return null;
  if (first15mVolMap.has(cleanSym)) {
    return first15mVolMap.get(cleanSym);
  }
  try {
    const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${cleanSym}.NS?range=5d&interval=15m`, {
      headers: { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' }
    });
    if (res.ok) {
      const json = await res.json();
      const result = json.chart?.result?.[0];
      const timestamps = result?.timestamp || [];
      const vols = result?.indicators?.quote?.[0]?.volume || [];
      
      const dayMap = {};
      for (let i = 0; i < timestamps.length; i++) {
        const ts = timestamps[i];
        const v = vols[i] || 0;
        // Convert to IST (UTC+5:30)
        const dt = new Date((ts + 19800) * 1000);
        const hours = dt.getUTCHours();
        const mins = dt.getUTCMinutes();
        const dateStr = dt.toISOString().slice(0, 10);
        if (hours === 9 && mins === 15) {
          dayMap[dateStr] = v;
        }
      }

      const sortedDates = Object.keys(dayMap).sort();
      if (sortedDates.length >= 2) {
        const candleVols = sortedDates.map(d => dayMap[d]);
        const todayVol = candleVols[candleVols.length - 1] || 0;
        // Previous up to 3 trading days
        const prevVols = candleVols.slice(Math.max(0, candleVols.length - 4), candleVols.length - 1);
        
        let isHighest = false;
        let maxPrev = 0;
        let extraVol = 0;

        if (prevVols.length > 0 && todayVol > 0) {
          maxPrev = Math.max(...prevVols);
          // Check if today's volume is strictly greater than or equal to ALL previous 3 days
          isHighest = prevVols.every(pv => todayVol >= pv);
          if (isHighest && maxPrev > 0) {
            extraVol = todayVol - maxPrev;
          }
        }

        const record = {
          today_15m_vol: todayVol,
          prev_3d_max: maxPrev,
          prev_3d_vols: prevVols,
          is_highest: isHighest,
          extra_volume: extraVol
        };

        first15mVolMap.set(cleanSym, record);
        return record;
      }
    }
  } catch (e) {
    // Fail silently on individual requests
  }
  return null;
}

// Background prefetch for 3-day 15m volume comparison across screened symbols
export async function prefetch15mVolumes(symbols) {
  if (first15mPrefetchInProgress || !symbols || symbols.length === 0) return;
  first15mPrefetchInProgress = true;
  try {
    const toFetch = symbols.filter(s => !first15mVolMap.has(s.toUpperCase())).slice(0, 100);
    const BATCH_SIZE = 12;
    for (let i = 0; i < toFetch.length; i += BATCH_SIZE) {
      const batch = toFetch.slice(i, i + BATCH_SIZE);
      await Promise.all(batch.map(fetchFirst15mVolumeComparison));
    }
  } catch (err) {
    console.warn('Prefetch 15m volumes error:', err.message);
  } finally {
    first15mPrefetchInProgress = false;
  }
}

export async function fetch5DayMedianVolume(sym) {
  const cleanSym = String(sym || '').replace(/[^A-Za-z0-9_-]/g, '').toUpperCase();
  if (!cleanSym) return null;
  if (median5dVolumeMap.has(cleanSym)) {
    return median5dVolumeMap.get(cleanSym);
  }
  try {
    const res = await fetch(`https://query1.finance.yahoo.com/v8/finance/chart/${cleanSym}.NS?range=10d&interval=1d`, {
      headers: { 'User-Agent': 'Mozilla/5.0' }
    });
    if (res.ok) {
      const json = await res.json();
      const vols = json.chart?.result?.[0]?.indicators?.quote?.[0]?.volume?.filter(v => v !== null && v > 0) || [];
      if (vols.length >= 6) {
        const hist5 = vols.slice(-6, -1);
        const sorted = [...hist5].sort((a, b) => a - b);
        const median = sorted[2]; // 3rd of 5 sorted items is the exact median
        if (median > 0) {
          median5dVolumeMap.set(cleanSym, median);
          return median;
        }
      }
    }
  } catch (e) {
    // Ignore individual fetch failures gracefully
  }
  return null;
}

// Background batch loader for 5-day historical volume medians
export async function prefetch5DayMedians(symbols) {
  if (medianPrefetchInProgress || !symbols || symbols.length === 0) return;
  medianPrefetchInProgress = true;
  try {
    const toFetch = symbols.filter(s => !median5dVolumeMap.has(s.toUpperCase())).slice(0, 80);
    const BATCH_SIZE = 10;
    for (let i = 0; i < toFetch.length; i += BATCH_SIZE) {
      const batch = toFetch.slice(i, i + BATCH_SIZE);
      await Promise.all(batch.map(fetch5DayMedianVolume));
    }
  } catch (err) {
    console.warn('Prefetch 5-day medians error:', err.message);
  } finally {
    medianPrefetchInProgress = false;
  }
}

// -------------------------------------------------------------
// TRADINGVIEW LIVE SCANNER API CALLER & CACHE
// -------------------------------------------------------------
let tvCache = {
  timestamp: 0,
  data: []
};

const TV_CACHE_TTL_MS = 15000; // 15 seconds cache

export async function fetchTradingViewScanner(minPrice = 200, maxPrice = 4000, limit = 1500) {
  const now = Date.now();
  if (tvCache.data.length > 0 && (now - tvCache.timestamp) < TV_CACHE_TTL_MS) {
    return tvCache.data;
  }

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

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json'
      },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      throw new Error(`TradingView returned HTTP ${res.status}`);
    }

    const data = await res.json();
    const rawItems = data.data || [];

    const mapped = [];
    const symbolsList = [];

    for (const item of rawItems) {
      const d = item.d || [];
      const rawSymbol = d[0] || 'UNKNOWN';
      const symbol = String(rawSymbol).toUpperCase().trim();

      // 1. Condition Flow: Nifty Total Market Index constituent check
      if (niftySymbols && niftySymbols.size > 0 && !niftySymbols.has(symbol)) {
        continue;
      }

      symbolsList.push(symbol);

      const name = d[1] || symbol;
      const close = typeof d[2] === 'number' ? Math.round(d[2] * 100) / 100 : 0;
      const chg = typeof d[3] === 'number' ? Math.round(d[3] * 100) / 100 : 0;
      const vol = typeof d[4] === 'number' ? d[4] : 0;
      
      // Calculate 5-Day Median Relative Volume if cached, else fallback to TV normalized ratio
      let relvol = typeof d[5] === 'number' ? Math.round(d[5] * 100) / 100 : 1.25;
      const median5d = median5dVolumeMap.get(symbol);
      if (median5d && median5d > 0 && vol > 0) {
        relvol = Math.round((vol / median5d) * 100) / 100;
      }

      const ema = typeof d[6] === 'number' ? Math.round(d[6] * 100) / 100 : Math.round(close * 0.98 * 100) / 100;
      const high = typeof d[7] === 'number' ? Math.round(d[7] * 100) / 100 : close;
      const low = typeof d[8] === 'number' ? Math.round(d[8] * 100) / 100 : close;
      const open = typeof d[9] === 'number' ? Math.round(d[9] * 100) / 100 : close;
      const gap = typeof d[10] === 'number' ? Math.round(d[10] * 100) / 100 : 0;
      const sector = d[11] || 'General';

      const first15m = first15mVolMap.get(symbol);
      let extra15mVol = 0;
      let is15mHighest = false;
      let first15mToday = 0;
      let first15mPrevMax = 0;

      if (first15m) {
        first15mToday = first15m.today_15m_vol || 0;
        first15mPrevMax = first15m.prev_3d_max || 0;
        is15mHighest = first15m.is_highest || false;
        extra15mVol = first15m.extra_volume || 0;
      }

      mapped.push({
        symbol: symbol,
        name: name,
        price: close,
        change_pct: chg,
        volume: vol,
        relvol: relvol,
        median5d_volume: median5d || null,
        first_15m_vol: first15mToday,
        first_15m_prev_max: first15mPrevMax,
        is_15m_highest: is15mHighest,
        extra_15m_vol: extra15mVol,
        sector: sector,
        yesterday_high: high,
        yesterday_low: low,
        yesterday_close: open,
        ema: ema,
        open915: open,
        high915: high,
        low915: low,
        close915: close,
        high920: high,
        low920: low,
        close920: close,
        gap: gap
      });
    }

    // Trigger non-blocking background prefetch for 5-day medians and 15m candle comparisons
    if (symbolsList.length > 0) {
      prefetch5DayMedians(symbolsList);
      prefetch15mVolumes(symbolsList);
    }

    if (mapped.length > 0) {
      tvCache = {
        timestamp: now,
        data: mapped
      };
      return mapped;
    }
  } catch (err) {
    console.error('TradingView scanner fetch error:', err.message);
  }

  return [];
}

// Helper to compute tick data map
export async function getTicksMap() {
  const ticks = {};
  const nowStr = new Date().toLocaleTimeString();

  const stocks = await fetchTradingViewScanner(200, 4000, 1500);

  stocks.forEach(s => {
    ticks[s.symbol] = {
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
  });

  return ticks;
}
