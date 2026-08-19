import express from 'express';
import cors from 'cors';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = 3000;

app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Load seed watchlist and cache data if available
let watchlistSymbols = [];
try {
  const wlPath = path.join(__dirname, 'stocks', 'watchlist.json');
  if (fs.existsSync(wlPath)) {
    const wl = JSON.parse(fs.readFileSync(wlPath, 'utf-8'));
    if (wl.symbols) {
      watchlistSymbols = Object.keys(wl.symbols);
    }
  }
} catch (e) {
  console.error('Error loading watchlist.json:', e.message);
}

if (watchlistSymbols.length === 0) {
  watchlistSymbols = [
    'RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'BHARTIARTL',
    'TATAMOTORS', 'LT', 'ITC', 'ADANIENSOL', 'FEDERALBNK', 'LUPIN', 'IGL',
    'PTC', 'ZOTA', 'GNFC', 'SHOPERSTOP', 'KENNAMET', 'KANSAINER', 'SUNPHARMA',
    'BAJFINANCE', 'AXISBANK', 'MARUTI', 'WIPRO', 'TITAN', 'KOTAKBANK', 'ASIANPAINT'
  ];
}

// In-memory Mock State
let activeBroker = 'dhan';
let brokerConnected = true;
let mockUser = {
  id: 'usr_smarttrader_01',
  email: 'trader@tradealgopro.com',
  name: 'Active Trader'
};

// -------------------------------------------------------------
// TRADINGVIEW SCANNER MODULE INTEGRATION
// (Consolidated in /tradingview/scanner.js)
// -------------------------------------------------------------
import {
  getNiftyTotalMarketSymbols,
  fetchTradingViewScanner,
  getTicksMap,
  getIngestionLogs,
  runManualIngestion,
  first5mCandleMap,
  first15mVolMap,
  persistCandleSnapshot
} from './tradingview/scanner.js';
import { syncAllSymbolsCandles } from './tradingview/tvFeed.js';
import { arrowStreamService } from './broker/arrow_stream_service.js';

// Auto-connect Arrow WebSocket stream for real-time market ticks
arrowStreamService.connect();

// Trigger background TV candle sync for key watchlist symbols on startup
setTimeout(async () => {
  try {
    const topSymbols = watchlistSymbols.slice(0, 50);
    console.log(`[TV-Feed] Background sync starting for ${topSymbols.length} symbols...`);
    const synced = await syncAllSymbolsCandles(topSymbols);
    for (const [sym, data] of Object.entries(synced)) {
      first15mVolMap.set(sym, data);
    }
    persistCandleSnapshot();
    console.log(`[TV-Feed] Background sync completed for ${Object.keys(synced).length} symbols.`);
  } catch (e) {
    console.warn('[TV-Feed] Background sync warning:', e.message);
  }
}, 2000);



// -------------------------------------------------------------
// AUTH ROUTES (Supabase Auth for User Credentials)
// -------------------------------------------------------------
const SUPABASE_URL = process.env.SUPABASE_URL || '';
const SUPABASE_KEY = process.env.SUPABASE_KEY || process.env.SUPABASE_ANON_KEY || '';

app.get(['/auth/me', '/api/auth/me'], async (req, res) => {
  const authHeader = req.headers.authorization || '';
  const token = authHeader.replace(/^Bearer\s+/i, '');

  if (SUPABASE_URL && SUPABASE_KEY && token && !token.startsWith('mock_')) {
    try {
      const resp = await fetch(`${SUPABASE_URL}/auth/v1/user`, {
        headers: {
          'apikey': SUPABASE_KEY,
          'Authorization': `Bearer ${token}`
        }
      });
      if (resp.ok) {
        const u = await resp.json();
        return res.json({
          ok: true,
          user: {
            id: u.id,
            email: u.email,
            name: u.user_metadata?.full_name || u.email?.split('@')[0] || 'Trader'
          }
        });
      }
    } catch (e) {
      console.warn('Supabase auth/me verification failed:', e.message);
    }
  }

  res.json({
    ok: true,
    user: mockUser
  });
});

app.post(['/auth/signup', '/api/auth/signup'], async (req, res) => {
  const { email, password, name } = req.body || {};
  if (!email) {
    return res.status(400).json({ ok: false, detail: 'Email is required' });
  }

  if (SUPABASE_URL && SUPABASE_KEY && password) {
    try {
      const resp = await fetch(`${SUPABASE_URL}/auth/v1/signup`, {
        method: 'POST',
        headers: {
          'apikey': SUPABASE_KEY,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          email,
          password,
          data: { full_name: name || email.split('@')[0] }
        })
      });
      const data = await resp.json();
      if (resp.ok) {
        mockUser = {
          id: data.user?.id || 'usr_' + Date.now(),
          email: data.user?.email || email,
          name: name || email.split('@')[0]
        };
        return res.json({
          ok: true,
          user: mockUser,
          access_token: data.access_token || ('jwt_' + Date.now()),
          token_type: 'bearer'
        });
      } else {
        return res.status(400).json({ ok: false, detail: data.msg || data.error_description || 'Signup failed' });
      }
    } catch (e) {
      console.warn('Supabase signup call error:', e.message);
    }
  }

  mockUser = {
    id: 'usr_' + Date.now(),
    email: email,
    name: name || email.split('@')[0]
  };
  res.json({
    ok: true,
    user: mockUser,
    access_token: 'mock_jwt_token_' + Date.now(),
    token_type: 'bearer'
  });
});

app.post(['/auth/signin', '/api/auth/signin'], async (req, res) => {
  const { email, password } = req.body || {};
  if (!email) {
    return res.status(400).json({ ok: false, detail: 'Email is required' });
  }

  if (SUPABASE_URL && SUPABASE_KEY && password) {
    try {
      const resp = await fetch(`${SUPABASE_URL}/auth/v1/token?grant_type=password`, {
        method: 'POST',
        headers: {
          'apikey': SUPABASE_KEY,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ email, password })
      });
      const data = await resp.json();
      if (resp.ok) {
        mockUser = {
          id: data.user?.id || 'usr_' + Date.now(),
          email: data.user?.email || email,
          name: data.user?.user_metadata?.full_name || email.split('@')[0]
        };
        return res.json({
          ok: true,
          user: mockUser,
          access_token: data.access_token,
          token_type: 'bearer'
        });
      } else {
        return res.status(400).json({ ok: false, detail: data.error_description || data.msg || 'Invalid credentials' });
      }
    } catch (e) {
      console.warn('Supabase signin call error:', e.message);
    }
  }

  mockUser = {
    id: 'usr_' + Date.now(),
    email: email,
    name: email.split('@')[0]
  };
  res.json({
    ok: true,
    user: mockUser,
    access_token: 'mock_jwt_token_' + Date.now(),
    token_type: 'bearer'
  });
});

app.post(['/auth/logout', '/api/auth/logout'], (req, res) => {
  res.json({ ok: true, message: 'Logged out successfully' });
});

// -------------------------------------------------------------
// TRADINGVIEW DEDICATED SCANNER ENDPOINT
// -------------------------------------------------------------
app.get('/api/tradingview/scan', async (req, res) => {
  const minPrice = parseFloat(req.query.min_price) || 200;
  const maxPrice = parseFloat(req.query.max_price) || 4000;
  const limit = parseInt(req.query.limit) || 60;
  try {
    const stocks = await fetchTradingViewScanner(minPrice, maxPrice, limit);
    res.json({
      success: true,
      source: 'TradingView Scanner API',
      total: stocks.length,
      data: stocks
    });
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});

app.get(['/api/tradingview/ingestion/status', '/api/ingestion/status'], (req, res) => {
  res.json({
    success: true,
    active_date: new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }),
    candle_15min_stocks: first15mVolMap.size,
    candle_5min_stocks: first5mCandleMap.size,
    timestamp: new Date().toISOString()
  });
});

app.get(['/api/tradingview/ingestion/logs', '/api/ingestion/logs'], (req, res) => {
  const limit = parseInt(req.query.limit) || 50;
  res.json({
    success: true,
    logs: getIngestionLogs(limit)
  });
});

app.post(['/api/tradingview/ingestion/trigger', '/api/ingestion/trigger'], async (req, res) => {
  try {
    const result = await runManualIngestion();
    res.json({
      success: true,
      message: 'Ingestion pipeline executed successfully',
      result
    });
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});

// -------------------------------------------------------------
// MARKET & TICKS ROUTES
// -------------------------------------------------------------
app.get('/api/market/indices', (req, res) => {
  const now = new Date();
  res.json({
    indices: [
      {
        name: 'NIFTY 50',
        ltp: 24385.40 + Math.round((Math.random() - 0.5) * 10 * 100) / 100,
        change_pct: 0.48,
        change: 116.20,
        timestamp: now.toLocaleTimeString()
      },
      {
        name: 'BANKNIFTY',
        ltp: 51240.80 + Math.round((Math.random() - 0.5) * 20 * 100) / 100,
        change_pct: 0.35,
        change: 178.60,
        timestamp: now.toLocaleTimeString()
      }
    ],
    connected: brokerConnected
  });
});

app.get('/api/market/live-ticks', async (req, res) => {
  const ticks = await getTicksMap();
  res.json({
    connected: brokerConnected,
    ticks: ticks
  });
});

// SSE streams with high-frequency real-time push
app.get(['/api/market/live-ticks/stream', '/api/market/bigplayers-ticks/stream'], (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const sendTick = async () => {
    try {
      const ticks = await getTicksMap();
      const data = JSON.stringify({
        connected: brokerConnected || arrowStreamService.isConnected,
        broker: arrowStreamService.isConnected ? 'arrow' : 'angel',
        ticks: ticks
      });
      res.write(`data: ${data}\n\n`);
    } catch (e) {
      console.error('SSE send error:', e.message);
    }
  };

  sendTick();
  // 300ms push cadence for sub-second millisecond responsiveness
  const interval = setInterval(sendTick, 300);

  req.on('close', () => {
    clearInterval(interval);
  });
});

// -------------------------------------------------------------
// STRATEGIES ROUTES (Advance ORB, Big Players)
// -------------------------------------------------------------
const ADVANCE_ORB_COLUMNS = [
  'Symbol', 'Price', 'CHG%', 'Signal', 'Extra 15m Vol', '200 EMA', '1st High', '1st Low',
  '1st Range%', 'Inside 9:15', 'GAP%', 'Volume', 'RELVOL', 'Sector', 'MaxQty', 'Action'
];

const BIG_PLAYERS_COLUMNS = [
  'Symbol', 'Price', 'CHG%', 'Breakout', 'SupportPrice', 'EntryPrice',
  'SL', 'MaxQty', 'RiskRs', 'TodayLow'
];

function computeMaxQty(budget, parts, price) {
  if (!budget || !parts || !price || price <= 0) return 0;
  const partBudget = budget / parts;
  // Standard 5x intraday margin multiplier
  const marginMultiplier = 5;
  return Math.floor((partBudget * marginMultiplier) / price);
}

app.get('/api/strategies/advanceorb', async (req, res) => {
  const budget = parseFloat(req.query.budget) || 100000;
  const parts = parseFloat(req.query.parts) || 5;
  const filterByToggle = req.query.filter_by_toggle === 'true';
  const nearHigh = req.query.near_high === 'true';
  const aboveEma = req.query.above_ema === 'true';
  const inside915 = req.query.inside915 === 'true';
  const inside3 = req.query.inside3 === 'true';

  const rawStocks = await fetchTradingViewScanner(200, 4000, 1500);

  // Subscribe all scanned stocks to Arrow Trade WebSocket for live tick updates
  arrowStreamService.subscribeSymbols(rawStocks.map(s => s.symbol));

  let mapped = rawStocks.map(s => {
    const liveTick = arrowStreamService.getTick(s.symbol);
    const livePrice = (liveTick && liveTick.ltp > 0) ? liveTick.ltp : s.price;
    const liveChg = (liveTick && liveTick.change_pct != null) ? liveTick.change_pct : s.change_pct;
    const liveVol = (liveTick && liveTick.volume > 0) ? liveTick.volume : s.volume;

    const gap = s.gap || (s.open915 && s.yesterday_close ? Math.round(((s.open915 - s.yesterday_close) / s.yesterday_close) * 10000) / 100 : 0);
    const rangePct = s.high915 && s.low915 && s.low915 > 0 ? Math.round(((s.high915 - s.low915) / s.low915) * 10000) / 100 : 1.5;
    const isInside915 = rangePct <= 2.8;
    const isAboveEma = livePrice >= (s.ema || 0);
    const prevHigh = s.yesterday_high || s.high915;
    const isNearHigh = livePrice >= prevHigh ? true : ((prevHigh - livePrice) / prevHigh <= 0.02);
    const maxQty = computeMaxQty(budget, parts, livePrice);


    return {
      Symbol: s.symbol,
      Price: livePrice,
      'CHG%': liveChg,
      'GAP%': gap,
      Volume: liveVol,
      RELVOL: s.relvol || 1.45,
      Sector: s.sector || 'General',
      '200 EMA': s.ema,
      ema: s.ema,
      ema_15m: s.ema_15m,
      ema_5m: s.ema_5m,
      ema_daily: s.ema_daily,
      ema200: s.ema,
      '1st High': s.high915,
      '1st Low': s.low915,
      '1st Range%': rangePct,
      'Inside 9:15': isInside915 ? 'Yes' : 'No',
      'Share Low': s.low915,

      'Extra 15m Vol': s.extra_15m_vol || 0,
      extra_15m_vol: s.extra_15m_vol || 0,
      first_15m_vol: s.first_15m_vol || 0,
      first_15m_prev_max: s.first_15m_prev_max || 0,
      is_15m_highest: s.is_15m_highest || false,
      d1_vol: s.d1_vol || 0,
      d2_vol: s.d2_vol || 0,
      d3_vol: s.d3_vol || 0,
      d1: s.d1_vol || 0,
      d2: s.d2_vol || 0,
      d3: s.d3_vol || 0,
      prev_3d_vols: [s.d1_vol || 0, s.d2_vol || 0, s.d3_vol || 0],
      MaxQty: maxQty,
      open915: s.open915,
      yesterday_high: s.yesterday_high,
      yesterday_low: s.yesterday_low,
      yesterday_close: s.yesterday_close,
      high915: s.high915,
      low915: s.low915,
      close915: s.close915,
      candle_range_pct: rangePct,
      close920: s.close920,
      inside_915: isInside915,
      above_ema: isAboveEma,
      near_high: isNearHigh
    };
  });

  let data = mapped;
  // Sort descending by CHG% (highest to lowest)
  data.sort((a, b) => (parseFloat(b['CHG%']) || 0) - (parseFloat(a['CHG%']) || 0));

  // If explicitly requested to filter backend-side (e.g. CLI/export)
  if (filterByToggle) {
    if (aboveEma) data = data.filter(item => item.above_ema);
    if (inside915) data = data.filter(item => item.inside_915);
    if (nearHigh) data = data.filter(item => item.near_high);
    if (inside3) data = data.filter(item => item['1st Range%'] <= 3.0);
  }

  res.json({
    strategy: 'advanceorb',
    name: 'Advance ORB',
    count: data.length,
    data: data,
    columns: ADVANCE_ORB_COLUMNS,
    conditions: {
      price_range: '200 - 4000 INR',
      gap_threshold: '< 2.0%',
      min_market_cap: '41B INR',
      above_200_ema: aboveEma,
      inside_915: inside915
    },
    source: 'TradingView Scanner API',
    candle_data_available: true,
    market_closed: false,
    reference_date: new Date().toISOString().slice(0, 10)
  });
});

app.post('/api/strategies/advanceorb/qty', async (req, res) => {
  const { budget, parts } = req.body || {};
  const b = parseFloat(budget) || 100000;
  const p = parseFloat(parts) || 5;

  const stocks = await fetchTradingViewScanner(200, 4000, 1500);

  const data = stocks.map(s => ({
    Symbol: s.symbol,
    MaxQty: computeMaxQty(b, p, s.price)
  }));

  res.json({ data });
});

app.all('/api/strategies/advanceorb/refresh', async (req, res) => {
  let requestedTickers = [];
  if (Array.isArray(req.body?.tickers)) {
    requestedTickers = req.body.tickers.map(t => String(t).trim().toUpperCase());
  } else if (typeof req.query?.tickers === 'string' && req.query.tickers) {
    requestedTickers = req.query.tickers.split(',').map(t => t.trim().toUpperCase());
  }
  
  const stocks = await fetchTradingViewScanner(200, 4000, 1500);

  const filtered = requestedTickers.length > 0 
    ? stocks.filter(s => requestedTickers.includes(s.symbol.toUpperCase()))
    : stocks;

  const refreshed = filtered.map(s => {
    const liveTick = arrowStreamService.getTick(s.symbol);
    const livePrice = (liveTick && liveTick.ltp > 0) ? liveTick.ltp : s.price;
    const liveChg = (liveTick && liveTick.change_pct != null) ? liveTick.change_pct : s.change_pct;
    const rangePct = s.high915 && s.low915 && s.low915 > 0 ? Math.round(((s.high915 - s.low915) / s.low915) * 10000) / 100 : 1.5;
    const isInside915 = rangePct <= 2.8;
    return {
      Symbol: s.symbol,
      Price: livePrice,
      'CHG%': liveChg,
      Volume: typeof s.volume === 'number' ? s.volume.toLocaleString('en-IN') : s.volume,
      RELVOL: `${s.relvol || 1.25}x`,
      Sector: s.sector || 'General',
      '200 EMA': s.ema,
      '1st High': s.high915,
      '1st Low': s.low915,
      '1st Range%': rangePct,
      inside_915: isInside915,
      close920: s.close920 || livePrice
    };
  });

  res.json({ refreshed });
});


app.get('/api/strategies/bigplayers', async (req, res) => {
  const budget = parseFloat(req.query.budget) || 100000;
  const parts = parseFloat(req.query.parts) || 5;
  const newLowToggle = req.query.new_low === 'true' || req.query.newlow === 'true' || req.query.broke_915_low === 'true';

  const stocks = await fetchTradingViewScanner(200, 4000, 1500);

  // Subscribe all symbols to the unified WebSocket
  arrowStreamService.subscribeSymbols(stocks.map(s => s.symbol));

  let data = stocks.map(s => {
    const liveTick = arrowStreamService.getTick(s.symbol);
    const livePrice = (liveTick && liveTick.ltp > 0) ? liveTick.ltp : s.price;
    const liveChg = (liveTick && liveTick.change_pct != null) ? liveTick.change_pct : s.change_pct;
    const entryPrice = livePrice;
    const sl = Math.round((entryPrice * 0.99) * 100) / 100;
    const maxQty = computeMaxQty(budget, parts, livePrice);
    const riskRs = Math.round((entryPrice - sl) * maxQty);

    const candle15m = first15mVolMap.get(s.symbol);
    const candle5m = first5mCandleMap.get(s.symbol);

    const low915 = s.low915 || candle15m?.low915 || candle15m?.price_0915_L || (livePrice * 0.99);
    const high915 = s.high915 || candle15m?.high915 || candle15m?.price_0915_H || (livePrice * 1.01);
    const lowestLowTill1015 = candle15m?.lowest_low_till_1015 || low915;
    const highestHighTill1015 = candle15m?.highest_high_till_1015 || high915;

    // TodayLow considers 9:15 low, subsequent candles till 10:15, and live tick price
    const todayLow = Math.round(Math.min(low915, lowestLowTill1015, livePrice) * 100) / 100;
    const todayHigh = Math.round(Math.max(high915, highestHighTill1015, livePrice) * 100) / 100;

    const broke915Low = todayLow < low915 || livePrice < low915 || candle15m?.broke_915_low === true || candle15m?.new_low_formed === true;
    const broke915High = todayHigh > high915 || livePrice > high915 || candle15m?.broke_915_high === true || candle15m?.new_high_formed === true;

    return {
      Symbol: s.symbol,
      Price: livePrice,
      'CHG%': liveChg,
      Breakout: livePrice >= (high915 * 0.995) ? 'Confirmed' : 'Forming',
      SupportPrice: low915,
      EntryPrice: entryPrice,
      SL: sl,
      MaxQty: maxQty,
      RiskRs: riskRs,
      TodayLow: todayLow,
      TodayHigh: todayHigh,
      low915: low915,
      high915: high915,
      open915: s.open915 || candle15m?.open915 || livePrice,
      close915: s.close915 || candle15m?.close915 || livePrice,
      lowest_low_till_1015: lowestLowTill1015,
      highest_high_till_1015: highestHighTill1015,
      new_low_formed: broke915Low,
      broke_915_low: broke915Low,
      new_high_formed: broke915High,
      broke_915_high: broke915High,
      candles_15m_till_1015: candle15m?.candles_till_1015 || null,
      candles_5m_till_1015: candle5m?.candles_till_1015 || null
    };
  });

  // Apply new low toggle filter if active
  if (newLowToggle) {
    data = data.filter(item => item.new_low_formed || item.broke_915_low || item.TodayLow < item.low915);
  }

  // Sort descending by CHG% (highest to lowest)
  data.sort((a, b) => (parseFloat(b['CHG%']) || 0) - (parseFloat(a['CHG%']) || 0));

  res.json({
    strategy: 'bigplayers',
    name: 'Big Players',
    count: data.length,
    data: data,
    columns: BIG_PLAYERS_COLUMNS,
    source: 'TradingView Candle Feed API',
    filter_new_low: newLowToggle,
    conditions: {
      support_bounce: true,
      volume_breakout: true,
      new_low_tracking: true
    }
  });
});

app.post('/api/strategies/bigplayers/qty', async (req, res) => {
  const { budget, parts } = req.body || {};
  const b = parseFloat(budget) || 100000;
  const p = parseFloat(parts) || 5;

  const stocks = await fetchTradingViewScanner(200, 4000, 1500);

  const data = stocks.map(s => ({
    Symbol: s.symbol,
    MaxQty: computeMaxQty(b, p, s.price)
  }));

  res.json({ data });
});

app.get('/api/strategies/bigplayers/refresh', async (req, res) => {
  const tickersParam = req.query.tickers || '';
  const requestedTickers = tickersParam ? tickersParam.split(',').map(t => t.trim().toUpperCase()) : [];

  const stocks = await fetchTradingViewScanner(200, 4000, 1500);

  const filtered = requestedTickers.length > 0
    ? stocks.filter(s => requestedTickers.includes(s.symbol.toUpperCase()))
    : stocks;

  const refreshed = filtered.map(s => {
    const entryPrice = s.price;
    const sl = Math.round((entryPrice * 0.99) * 100) / 100;
    return {
      Symbol: s.symbol,
      symbol: s.symbol,
      Price: entryPrice,
      price: entryPrice,
      'CHG%': s.change_pct,
      chg: s.change_pct,
      Breakout: entryPrice >= (s.high915 * 0.995) ? 'Confirmed' : 'Forming',
      SupportPrice: s.low915,
      supportPrice: s.low915,
      TodayLow: s.low915,
      low915: s.low915,
      high915: s.high915,
      SL: sl
    };
  });

  res.json({ refreshed });
});

// Direct TradingView candle sync endpoints
app.post(['/api/tradingview/sync-candles', '/api/screener/sync-candles'], async (req, res) => {
  try {
    const symbols = req.body.symbols || watchlistSymbols.slice(0, 60);
    const synced = await syncAllSymbolsCandles(symbols);
    for (const [sym, data] of Object.entries(synced)) {
      first15mVolMap.set(sym, data);
    }
    persistCandleSnapshot();
    res.json({ success: true, count: Object.keys(synced).length, synced });
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});

app.get(['/api/tradingview/candle-0915', '/api/screener/candle-0915'], (req, res) => {
  try {
    const jsonPath = path.join(__dirname, 'json', 'candle_15min.json');
    if (fs.existsSync(jsonPath)) {
      const data = JSON.parse(fs.readFileSync(jsonPath, 'utf8'));
      return res.json(data);
    }
    res.json({});
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// Dedicated Candle Timeline API (Track all 5m & 15m candles from 09:15 to 10:15)
app.get(['/api/candles/timeline', '/api/tradingview/candles/timeline'], (req, res) => {
  const sym = (req.query.symbol || '').toUpperCase().trim();
  const tf = req.query.timeframe === '5m' ? '5m' : '15m';

  if (sym) {
    const candle15m = first15mVolMap.get(sym);
    const candle5m = first5mCandleMap.get(sym);
    return res.json({
      success: true,
      symbol: sym,
      timeframe: tf,
      open915: candle15m?.open915,
      high915: candle15m?.high915,
      low915: candle15m?.low915,
      close915: candle15m?.close915,
      lowest_low_till_1015: candle15m?.lowest_low_till_1015,
      highest_high_till_1015: candle15m?.highest_high_till_1015,
      broke_915_low: candle15m?.broke_915_low,
      new_low_formed: candle15m?.new_low_formed,
      candles_15m_till_1015: candle15m?.candles_till_1015 || null,
      candles_5m_till_1015: candle5m?.candles_till_1015 || null
    });
  }

  // Entire universe summary
  const summary = [];
  first15mVolMap.forEach((v, s) => {
    summary.push({
      symbol: s,
      open915: v.open915,
      high915: v.high915,
      low915: v.low915,
      close915: v.close915,
      lowest_low_till_1015: v.lowest_low_till_1015,
      highest_high_till_1015: v.highest_high_till_1015,
      broke_915_low: v.broke_915_low,
      new_low_formed: v.new_low_formed,
      candles_count_15m: v.candles_till_1015 ? Object.keys(v.candles_till_1015).length : 0
    });
  });

  res.json({
    success: true,
    total_stocks: summary.length,
    tracking_window: '09:15 to 10:15 IST',
    candles_15m_intervals: ['09:15', '09:30', '09:45', '10:00'],
    candles_5m_intervals: ['09:15', '09:20', '09:25', '09:30', '09:35', '09:40', '09:45', '09:50', '09:55', '10:00', '10:05', '10:10'],
    data: summary
  });
});

// -------------------------------------------------------------
// ORDERS & PORTFOLIO ROUTES
// -------------------------------------------------------------
const mockPositions = [
  {
    tradingSymbol: 'TATAMOTORS',
    productType: 'INTRADAY',
    netQty: 50,
    buyAvg: 975.20,
    ltp: 984.30,
    mtm: 455.00,
    unrealizedPnl: 455.00,
    realizedPnl: 0,
    buyAmount: 48760.00,
    sellAmount: 0
  },
  {
    tradingSymbol: 'ADANIENSOL',
    productType: 'INTRADAY',
    netQty: 30,
    buyAvg: 1608.50,
    ltp: 1616.00,
    mtm: 225.00,
    unrealizedPnl: 225.00,
    realizedPnl: 0,
    buyAmount: 48255.00,
    sellAmount: 0
  }
];

const mockHoldings = [
  {
    tradingSymbol: 'RELIANCE',
    totalQty: 25,
    averagePrice: 2850.00,
    ltp: 2985.50,
    pnl: 3387.50
  },
  {
    tradingSymbol: 'INFY',
    totalQty: 40,
    averagePrice: 1780.00,
    ltp: 1845.20,
    pnl: 2608.00
  }
];

app.get('/api/portfolio/funds', (req, res) => {
  res.json({
    success: true,
    broker: activeBroker,
    data: {
      availabelBalance: 135400.00,
      availableCash: 135400.00,
      totalavailablemargin: 135400.00,
      sodLimit: 150000.00,
      utilizedAmount: 14600.00,
      marginUsed: 14600.00
    }
  });
});

app.get('/api/portfolio/holdings', (req, res) => {
  res.json({
    success: true,
    broker: activeBroker,
    data: mockHoldings
  });
});

app.get('/api/portfolio/positions', (req, res) => {
  res.json({
    success: true,
    broker: activeBroker,
    data: mockPositions
  });
});

app.post('/api/orders/place', (req, res) => {
  const { symbol, quantity } = req.body;
  const orderId = 'ORD_' + Date.now();
  res.json({
    success: true,
    order_id: orderId,
    symbol: symbol,
    quantity: quantity,
    message: `Order for ${symbol} placed successfully`
  });
});

app.post('/api/orders/place-batch', (req, res) => {
  const { orders = [] } = req.body;
  const results = orders.map((o, idx) => ({
    order_id: 'ORD_BATCH_' + Date.now() + '_' + idx,
    symbol: o.symbol,
    status: 'PLACED',
    quantity: o.quantity,
    stopLoss: o.stopLoss || null
  }));

  res.json({
    source: 'auto_buy',
    total: orders.length,
    succeeded: orders.length,
    failed: 0,
    slPlaced: orders.filter(o => o.stopLoss).length,
    results: results
  });
});

app.post('/api/orders/trail-sl', (req, res) => {
  const { order_id, new_trigger } = req.body;
  res.json({
    success: true,
    order_id: order_id,
    new_trigger: new_trigger,
    message: 'Stop loss updated successfully'
  });
});

// -------------------------------------------------------------
// BROKER SETTINGS & STATUS ROUTES
// -------------------------------------------------------------
app.get('/api/broker/status', (req, res) => {
  res.json({
    connected: brokerConnected,
    broker: activeBroker,
    client_id_masked: activeBroker === 'dhan' ? 'DHAN***408' : 'ANGEL***912'
  });
});

app.post('/api/broker/connect', (req, res) => {
  const { broker } = req.body;
  activeBroker = broker || 'dhan';
  brokerConnected = true;
  res.json({
    ok: true,
    connected: true,
    broker: activeBroker,
    message: `Connected to ${activeBroker.toUpperCase()} successfully`
  });
});

app.post('/api/broker/disconnect', (req, res) => {
  brokerConnected = false;
  res.json({
    ok: true,
    connected: false,
    message: 'Broker disconnected'
  });
});

app.post('/api/broker/refresh-token', (req, res) => {
  res.json({
    ok: true,
    message: 'Token refreshed successfully'
  });
});

// -------------------------------------------------------------
// NIFTY OHLC & CANDLES ROUTES
// -------------------------------------------------------------
app.get('/api/nifty/ohlc', async (req, res) => {
  const now = new Date();
  const stocks = await fetchTradingViewScanner(200, 4000, 15);

  const rows = stocks.slice(0, 10).map(s => {
    const vwap = Math.round(((s.high915 + s.low915 + s.price) / 3) * 100) / 100;
    return {
      symbol: s.symbol,
      name: s.name,
      price: s.price,
      open: s.open915,
      high: s.high915,
      low: s.low915,
      close: s.price,
      vwap: vwap,
      ema200: s.ema,
      volume: s.volume,
      change_pct: s.change_pct,
      candles: {
        '0915': { o: s.open915, h: s.high915, l: s.low915, c: s.price, vwap: vwap, vol: s.volume, ema: s.ema, chg: s.change_pct },
        '0920': { o: s.price, h: s.high915, l: s.low915, c: s.price, vwap: vwap, vol: Math.round(s.volume * 0.4), ema: s.ema, chg: s.change_pct }
      }
    };
  });

  const gainer = rows.reduce((max, r) => (r.change_pct > max.change_pct ? r : max), rows[0] || {});
  const loser = rows.reduce((min, r) => (r.change_pct < min.change_pct ? r : min), rows[0] || {});
  const aboveEma = rows.filter(r => r.ema200 && r.price > r.ema200);

  res.json({
    as_of: now.toISOString(),
    as_of_display: now.toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' }),
    market: { open: true, label: 'Open', note: 'Market is open (09:15 - 15:30 IST)' },
    refresh_seconds: 30,
    rows: rows,
    stored: rows.length,
    candle_labels: ['0915', '0920'],
    source: 'TradingView Scanner API',
    stats: {
      gainer: gainer,
      loser: loser,
      above_ema: {
        count: aboveEma.length,
        total: rows.length,
        symbols: aboveEma.map(r => r.symbol)
      }
    },
    error: null
  });
});

app.get(['/api/candles/0915', '/api/candles/snapshot', '/api/candles/15min', '/api/candles/0915_15min'], (req, res) => {
  const p1 = path.join(__dirname, 'json', 'candle_15min.json');
  let data = null;
  if (fs.existsSync(p1)) {
    try { data = JSON.parse(fs.readFileSync(p1, 'utf8')); } catch (e) {}
  }
  res.json({
    ok: true,
    timeframe: '15m',
    candle_time: '09:15',
    data: data || {},
    timestamp: new Date().toISOString()
  });
});

app.get(['/api/candles/5min', '/api/candles/0915_5min'], (req, res) => {
  const p1 = path.join(__dirname, 'json', 'candle_5min.json');
  let data = null;
  if (fs.existsSync(p1)) {
    try { data = JSON.parse(fs.readFileSync(p1, 'utf8')); } catch (e) {}
  }
  res.json({
    ok: true,
    timeframe: '5m',
    candle_time: '09:15',
    data: data || {},
    timestamp: new Date().toISOString()
  });
});

app.get('/api/candles/status', (req, res) => {
  const p15 = path.join(__dirname, 'json', 'candle_15min.json');
  const p5 = path.join(__dirname, 'json', 'candle_5min.json');
  
  let count15 = first15mVolMap.size;
  let count5 = first5mCandleMap.size;
  let lastModified = new Date().toISOString();

  if (fs.existsSync(p15)) {
    try {
      const stat = fs.statSync(p15);
      lastModified = stat.mtime.toISOString();
      const content = JSON.parse(fs.readFileSync(p15, 'utf8'));
      if (content.__meta__?.stock_count) count15 = content.__meta__.stock_count;
    } catch (e) {}
  }

  res.json({
    status: 'active',
    count_15m: count15,
    count_5m: count5,
    files: {
      candle_15min_exists: fs.existsSync(p15),
      candle_5min_exists: fs.existsSync(p5)
    },
    last_recorded: lastModified
  });
});

app.get('/api/candles/logs', (req, res) => {
  const limit = parseInt(req.query.limit || '100', 10);
  res.json({
    ok: true,
    total_logs: getIngestionLogs(limit).length,
    logs: getIngestionLogs(limit)
  });
});

app.post('/api/candles/refresh', async (req, res) => {
  try {
    const result = await runManualIngestion();
    res.json({
      ok: true,
      message: 'Ingestion pipeline executed successfully',
      result: result
    });
  } catch (err) {
    res.status(500).json({
      ok: false,
      error: err.message
    });
  }
});

app.get('/api/broker/angel/ws-status', (req, res) => {
  res.json({
    broker: 'angelone',
    websocket_endpoint: 'wss://smartapisocket.angelone.in/smart-stream',
    protocol: 'SmartStream V2 (Binary)',
    supported_modes: ['LTP (Mode 1)', 'Quote (Mode 2)', 'SnapQuote (Mode 3)'],
    status: brokerConnected && activeBroker === 'angel' ? 'CONNECTED' : 'STANDBY'
  });
});


app.get('/api/cache/status', (req, res) => {
  res.json({
    cached: true,
    total: watchlistSymbols.length,
    updated_at: new Date().toISOString()
  });
});

app.post('/api/data/purge-old-day', (req, res) => {
  res.json({ success: true, message: 'Purged old day data successfully' });
});

app.get('/api/health', (req, res) => {
  res.json({ status: 'healthy', timestamp: new Date().toISOString(), source: 'TradingView Scanner API' });
});

app.get('/api', (req, res) => {
  res.json({
    status: 'ok',
    message: 'TradeAlgo Pro Strategy API',
    source: 'TradingView Scanner API',
    conditions: {
      price_range: '200 to 4000 INR',
      gap_threshold: '< 2.0%',
      market_cap: '> 41B INR',
      exchange: 'NSE'
    },
    strategies: ['advanceorb', 'bigplayers', 'smartmoney']
  });
});

// -------------------------------------------------------------
// STATIC FILES & SPA SERVING
// -------------------------------------------------------------
app.use(express.static(__dirname));
app.use('/stocks', express.static(path.join(__dirname, 'stocks')));

app.get('/style.css', (req, res) => {
  res.sendFile(path.join(__dirname, 'style.css'));
});

app.get('/login.html', (req, res) => {
  res.sendFile(path.join(__dirname, 'login.html'));
});

app.get(['/nifty/ohlc', '/nifty_ohlc.html', '/testing/nifty_ohlc.html'], (req, res) => {
  const ohlcPath = path.join(__dirname, 'testing', 'nifty_ohlc.html');
  if (fs.existsSync(ohlcPath)) {
    return res.sendFile(ohlcPath);
  }
  res.sendFile(path.join(__dirname, 'nifty_ohlc.html'));
});

app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'index.html'));
});

// Fallback for SPA routing
app.get('*', (req, res, next) => {
  if (req.path.startsWith('/api/') || req.path.startsWith('/auth/')) {
    return res.status(404).json({ error: 'Endpoint not found' });
  }
  res.sendFile(path.join(__dirname, 'index.html'));
});

app.listen(PORT, '0.0.0.0', () => {
  console.log(`TradeAlgo Pro server running on http://0.0.0.0:${PORT}`);
});
