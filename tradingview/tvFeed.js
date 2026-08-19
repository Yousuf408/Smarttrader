import TradingView from '@mathieuc/tradingview';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const JSON_DIR = path.join(__dirname, '..', 'json');

const CANDLE_15MIN_JSON_PATH = path.join(JSON_DIR, 'candle_15min.json');
const CANDLE_5MIN_JSON_PATH = path.join(JSON_DIR, 'candle_5min.json');

/**
 * TV Feed Module: Direct integration with TradingView charts WebSocket
 * Fetches real historical 15m/5m opening candles (9:15 AM) and computes D1, D2, D3 comparison.
 */

export async function fetchTvCandlesForSymbol(symbol, timeframe = '15', range = 350, existingClient = null) {
  return new Promise((resolve) => {
    let client = existingClient || new TradingView.Client();
    let chart = null;
    let timeout = null;
    let resolved = false;

    const cleanup = () => {
      if (timeout) clearTimeout(timeout);
      if (!existingClient && client) {
        try { client.end(); } catch (e) {}
      }
    };

    timeout = setTimeout(() => {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve(null);
      }
    }, 6000);

    try {
      chart = new client.Session.Chart();
      chart.setMarket(`NSE:${symbol.toUpperCase()}`, {
        timeframe: timeframe,
        range: range
      });

      chart.onUpdate(() => {
        if (!resolved && chart.periods && chart.periods.length > 0) {
          resolved = true;
          const periods = [...chart.periods];
          cleanup();
          resolve(periods);
        }
      });

      chart.onError(() => {
        if (!resolved) {
          resolved = true;
          cleanup();
          resolve(null);
        }
      });
    } catch (e) {
      if (!resolved) {
        resolved = true;
        cleanup();
        resolve(null);
      }
    }
  });
}

/**
 * Calculates 200 EMA across candle periods and captures the EMA value at the 9:15 AM candle
 */
export function calculate200EmaAt915(periods) {
  if (!periods || !Array.isArray(periods) || periods.length === 0) return 0;
  const sorted = [...periods].sort((a, b) => a.time - b.time);
  const length = Math.min(200, sorted.length);
  if (length < 1) return 0;

  const k = 2 / (200 + 1);
  let ema = 0;
  for (let i = 0; i < length; i++) {
    ema += (sorted[i].close || sorted[i].open || 0);
  }
  ema /= length;

  let emaAt915 = 0;
  for (let i = length; i < sorted.length; i++) {
    const p = sorted[i];
    const close = p.close || p.open || ema;
    ema = (close - ema) * k + ema;
    const d = new Date(p.time * 1000);
    const istHours = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
    const istMins = (d.getUTCMinutes() + 30) % 60;
    if (istHours === 9 && istMins === 15) {
      emaAt915 = Math.round(ema * 100) / 100;
    }
  }

  return emaAt915 || Math.round(ema * 100) / 100;
}

/**
 * Extracts 9:15 AM IST candles and calculates D1, D2, D3 volumes + 200 EMA at 9:15 candle
 */
export function process915Candles(periods, timeframe = '15m') {
  if (!periods || !Array.isArray(periods) || periods.length === 0) return null;

  // Filter 09:15 AM IST candles (03:45 AM UTC)
  const openCandles = [];
  for (const p of periods) {
    const d = new Date(p.time * 1000);
    const istHours = (d.getUTCHours() + 5 + Math.floor((d.getUTCMinutes() + 30) / 60)) % 24;
    const istMins = (d.getUTCMinutes() + 30) % 60;
    if (istHours === 9 && istMins === 15) {
      openCandles.push({
        timestamp: p.time,
        date: d.toISOString().split('T')[0],
        open: Math.round(p.open * 100) / 100,
        high: Math.round(p.max * 100) / 100,
        low: Math.round(p.min * 100) / 100,
        close: Math.round(p.close * 100) / 100,
        volume: p.volume || 0,
        vwap: Math.round(((p.max + p.min + p.close) / 3) * 100) / 100
      });
    }
  }

  if (openCandles.length === 0) return null;

  // Sort chronologically ascending
  openCandles.sort((a, b) => a.timestamp - b.timestamp);

  const latest = openCandles[openCandles.length - 1];
  const history = openCandles.slice(0, -1).reverse(); // [D1, D2, D3, ...]

  const d1 = history[0] ? history[0].volume : 0;
  const d2 = history[1] ? history[1].volume : 0;
  const d3 = history[2] ? history[2].volume : 0;
  const prev_3d_vols = [d1, d2, d3].filter(v => v > 0);
  const prev_3d_max = prev_3d_vols.length > 0 ? Math.max(...prev_3d_vols) : 0;

  const today_vol = latest.volume || 0;
  const is_highest = (today_vol > 0 && prev_3d_max > 0 && today_vol > prev_3d_max);
  const extra_volume = is_highest ? (today_vol - prev_3d_max) : 0;

  const ema200_0915 = calculate200EmaAt915(periods);

  return {
    date: latest.date,
    timeframe: timeframe,
    candle_time: '09:15',
    price_0915_O: latest.open,
    price_0915_H: latest.high,
    price_0915_L: latest.low,
    price_0915_C: latest.close,
    volume_0915: today_vol,
    open915: latest.open,
    high915: latest.high,
    low915: latest.low,
    close915: latest.close,
    today_15m_vol: today_vol,
    d1_vol: d1,
    d2_vol: d2,
    d3_vol: d3,
    prev_3d_vols: prev_3d_vols,
    prev_3d_max: prev_3d_max,
    is_highest: is_highest,
    extra_volume: extra_volume,
    vwap: latest.vwap,
    ema200_0915: ema200_0915,
    ema_0915: ema200_0915,
    ema: ema200_0915,
    ema200: ema200_0915
  };
}

/**
 * Batch syncs all symbols using concurrency pooling and shared client session,
 * and writes to all candle JSON files.
 */
export async function syncAllSymbolsCandles(symbols, onProgress = null) {
  if (!symbols || !Array.isArray(symbols) || symbols.length === 0) return {};

  const BATCH_SIZE = 8;
  const results15m = {};
  const total = symbols.length;
  let processed = 0;

  for (let i = 0; i < symbols.length; i += BATCH_SIZE) {
    const chunk = symbols.slice(i, i + BATCH_SIZE);
    let client = null;
    try {
      client = new TradingView.Client();
      await Promise.all(chunk.map(async (sym) => {
        try {
          const periods = await fetchTvCandlesForSymbol(sym, '15', 100, client);
          if (periods) {
            const candleData = process915Candles(periods, '15m');
            if (candleData) {
              results15m[sym.toUpperCase()] = {
                symbol: sym.toUpperCase(),
                ...candleData
              };
            }
          }
        } catch (e) {
          // Ignore individual error
        } finally {
          processed++;
          if (onProgress) onProgress(processed, total);
        }
      }));
    } catch (e) {
      // Ignore client error
    } finally {
      if (client) {
        try { client.end(); } catch (e) {}
      }
    }

    await new Promise((r) => setTimeout(r, 100));
  }

  // Save to active JSON target file
  try {
    if (Object.keys(results15m).length > 0) {
      if (!fs.existsSync(JSON_DIR)) fs.mkdirSync(JSON_DIR, { recursive: true });

      let existing = {};
      if (fs.existsSync(CANDLE_15MIN_JSON_PATH)) {
        try { existing = JSON.parse(fs.readFileSync(CANDLE_15MIN_JSON_PATH, 'utf8')); } catch (e) {}
      }
      const today = new Date().toISOString().split('T')[0];
      const merged = {
        __meta__: {
          timeframe: '15m',
          candle_time: '09:15',
          date: today,
          updated_at: new Date().toISOString(),
          stock_count: Object.keys(results15m).length
        },
        ...existing
      };
      for (const [sym, data] of Object.entries(results15m)) {
        merged[`${today}|${sym}`] = data;
      }
      const jsonStr = JSON.stringify(merged, null, 2);

      fs.writeFileSync(CANDLE_15MIN_JSON_PATH, jsonStr, 'utf8');
    }
  } catch (err) {
    console.error('Error saving updated candle JSON files:', err.message);
  }

  return results15m;
}
