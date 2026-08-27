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

// In-memory Broker & User State
let activeBroker = 'angel';
let brokerConnected = true;
let mockUser = {
  id: 'usr_smarttrader_01',
  email: 'yousufshaikh420@gmail.com',
  name: 'Angel One Trader'
};

// -------------------------------------------------------------
// TIMESTAMP & TICK STATE HELPERS
// -------------------------------------------------------------
export function formatTimestampWithMs(d = new Date()) {
  const istOffset = 5.5 * 60 * 60 * 1000;
  const istTime = new Date(d.getTime() + istOffset);
  const iso = istTime.toISOString();
  return iso.replace('T', ' ').replace('Z', '').slice(0, 23);
}

// In-memory live ticks store for incoming broker WebSocket feeds
let liveTicksMap = {};

export function updateLiveTick(token, tickData) {
  liveTicksMap[token] = { ...liveTicksMap[token], ...tickData, timestamp: formatTimestampWithMs() };
}

export function getLiveTicks() {
  return liveTicksMap;
}

function getTodayISTDate() {
  const d = new Date();
  const istOffset = 5.5 * 60 * 60 * 1000;
  const istTime = new Date(d.getTime() + istOffset);
  return istTime.toISOString().slice(0, 10);
}

// -------------------------------------------------------------
// AUTH ROUTES (Local Session Auth)
// -------------------------------------------------------------
app.get(['/auth/me', '/api/auth/me'], async (req, res) => {
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

  mockUser = {
    id: 'usr_' + Date.now(),
    email: email,
    name: name || email.split('@')[0]
  };
  res.json({
    ok: true,
    user: mockUser,
    access_token: 'jwt_token_' + Date.now(),
    token_type: 'bearer'
  });
});

app.post(['/auth/signin', '/api/auth/signin'], async (req, res) => {
  const { email, password } = req.body || {};
  if (!email) {
    return res.status(400).json({ ok: false, detail: 'Email is required' });
  }

  mockUser = {
    id: 'usr_' + Date.now(),
    email: email,
    name: email.split('@')[0]
  };
  res.json({
    ok: true,
    user: mockUser,
    access_token: 'jwt_token_' + Date.now(),
    token_type: 'bearer'
  });
});

app.post(['/auth/logout', '/api/auth/logout'], (req, res) => {
  res.json({ ok: true, message: 'Logged out successfully' });
});

// -------------------------------------------------------------
// MARKET SCANNER & INGESTION ENDPOINTS (Clean Feed API)
// -------------------------------------------------------------
app.get(['/api/tradingview/scan', '/api/market/scan'], async (req, res) => {
  const ticks = getLiveTicks();
  const list = Object.values(ticks);
  res.json({
    success: true,
    source: 'Live WebSocket Stream',
    total: list.length,
    data: list
  });
});

app.get(['/api/tradingview/ingestion/status', '/api/ingestion/status'], (req, res) => {
  const ticks = getLiveTicks();
  res.json({
    success: true,
    active_date: new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Kolkata' }),
    total_stocks: Object.keys(ticks).length,
    timestamp: new Date().toISOString()
  });
});

app.get(['/api/tradingview/ingestion/logs', '/api/ingestion/logs'], (req, res) => {
  res.json({
    success: true,
    logs: []
  });
});

app.post(['/api/tradingview/ingestion/trigger', '/api/ingestion/trigger'], async (req, res) => {
  res.json({
    success: true,
    message: 'Stream active - ticks streaming live from broker'
  });
});

// -------------------------------------------------------------
// MARKET & TICKS ROUTES
// -------------------------------------------------------------
app.get('/api/market/indices', (req, res) => {
  const nowStr = formatTimestampWithMs();

  res.json({
    indices: [
      {
        name: 'NIFTY 50',
        symbol: 'NIFTY',
        token: '99926000',
        ltp: 24820.50,
        change_pct: 0.48,
        change: 118.20,
        timestamp: nowStr
      },
      {
        name: 'BANKNIFTY',
        symbol: 'BANKNIFTY',
        token: '99926009',
        ltp: 51240.80,
        change_pct: 0.35,
        change: 178.60,
        timestamp: nowStr
      },
      {
        name: 'FINNIFTY',
        symbol: 'FINNIFTY',
        token: '99926037',
        ltp: 23650.15,
        change_pct: 0.42,
        change: 99.40,
        timestamp: nowStr
      },
      {
        name: 'MIDCPNIFTY',
        symbol: 'MIDCPNIFTY',
        token: '99926074',
        ltp: 12845.60,
        change_pct: 0.55,
        change: 70.30,
        timestamp: nowStr
      }
    ],
    broker: activeBroker,
    connected: brokerConnected
  });
});

app.get('/api/market/live-ticks', async (req, res) => {
  const ticks = getLiveTicks();
  res.json({
    broker: activeBroker,
    connected: brokerConnected,
    ticks: ticks
  });
});

// SSE streams for live tick updates
app.get(['/api/market/live-ticks/stream', '/api/market/bigplayers-ticks/stream'], (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  const sendTick = () => {
    try {
      const ticks = getLiveTicks();
      const data = JSON.stringify({
        connected: brokerConnected,
        broker: activeBroker,
        timestamp: formatTimestampWithMs(),
        ticks: ticks
      });
      res.write(`data: ${data}\n\n`);
    } catch (e) {
      console.error('SSE send error:', e.message);
    }
  };

  sendTick();
  const interval = setInterval(sendTick, 1000);

  req.on('close', () => {
    clearInterval(interval);
  });
});

// -------------------------------------------------------------
// STRATEGIES ROUTES (Advance ORB, Big Players)
// -------------------------------------------------------------
const ADVANCE_ORB_COLUMNS = [
  'Symbol', 'Price', 'Last Update', 'CHG%', 'Signal', 'Extra 15m Vol', '200 EMA', '1st High', '1st Low',
  '1st Range%', 'Inside 9:15', 'GAP%', 'Volume', 'RELVOL', 'Sector', 'MaxQty', 'Action'
];

const BIG_PLAYERS_COLUMNS = [
  'Symbol', 'Price', 'Last Update', 'CHG%', '9:15 High', '9:15 Low', 'Today Low',
  'New Low', 'Pullback (9:15)', 'Breakout', 'SL', 'MaxQty'
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

  const ticks = getLiveTicks();
  const rawStocks = Object.values(ticks);
  const nowStr = formatTimestampWithMs();

  let mapped = rawStocks.map(s => {
    const livePrice = Number(s.price || s.ltp || 0);
    const liveChg = Number(s.change_pct || s.chg_pct || 0);
    const liveVol = Number(s.volume || 0);

    const gap = s.gap || (s.open915 && s.yesterday_close ? Math.round(((s.open915 - s.yesterday_close) / s.yesterday_close) * 10000) / 100 : 0);
    const rangePct = s.high915 && s.low915 && s.low915 > 0 ? Math.round(((s.high915 - s.low915) / s.low915) * 10000) / 100 : 1.5;
    const isInside915 = rangePct <= 2.8;
    const isAboveEma = livePrice >= (s.ema || 0);
    const prevHigh = s.yesterday_high || s.high915 || livePrice;
    const isNearHigh = livePrice >= prevHigh ? true : ((prevHigh - livePrice) / prevHigh <= 0.02);
    const maxQty = computeMaxQty(budget, parts, livePrice);
    const timeLog = nowStr;

    return {
      Symbol: s.symbol || s.tradingSymbol,
      Price: livePrice,
      'Last Update': timeLog,
      'Time Log': timeLog,
      last_update: timeLog,
      time_log: timeLog,
      timestamp: timeLog,
      'CHG%': liveChg,
      'GAP%': gap,
      Volume: liveVol,
      RELVOL: s.relvol || 1.45,
      Sector: s.sector || 'General',
      '200 EMA': s.ema || 0,
      ema: s.ema || 0,
      '1st High': s.high915 || livePrice,
      '1st Low': s.low915 || livePrice,
      '1st Range%': rangePct,
      'Inside 9:15': isInside915 ? 'Yes' : 'No',
      'Share Low': s.low915 || livePrice,
      MaxQty: maxQty,
      inside_915: isInside915,
      above_ema: isAboveEma,
      near_high: isNearHigh
    };
  });

  let data = mapped;
  data.sort((a, b) => (parseFloat(b['CHG%']) || 0) - (parseFloat(a['CHG%']) || 0));

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
    source: 'Live WebSocket Stream'
  });
});

app.post('/api/strategies/advanceorb/qty', async (req, res) => {
  const { budget, parts } = req.body || {};
  const b = parseFloat(budget) || 100000;
  const p = parseFloat(parts) || 5;

  const ticks = getLiveTicks();
  const stocks = Object.values(ticks);

  const data = stocks.map(s => ({
    Symbol: s.symbol || s.tradingSymbol,
    MaxQty: computeMaxQty(b, p, Number(s.price || s.ltp || 0))
  }));

  res.json({ data });
});

app.all('/api/strategies/advanceorb/refresh', async (req, res) => {
  const ticks = getLiveTicks();
  const stocks = Object.values(ticks);

  const refreshed = stocks.map(s => {
    const livePrice = Number(s.price || s.ltp || 0);
    const liveChg = Number(s.change_pct || s.chg_pct || 0);
    return {
      Symbol: s.symbol || s.tradingSymbol,
      Price: livePrice,
      'CHG%': liveChg,
      Volume: typeof s.volume === 'number' ? s.volume.toLocaleString('en-IN') : s.volume,
      RELVOL: `${s.relvol || 1.25}x`,
      Sector: s.sector || 'General',
      '200 EMA': s.ema || 0,
      '1st High': s.high915 || livePrice,
      '1st Low': s.low915 || livePrice,
      '1st Range%': 1.5,
      inside_915: true,
      close920: livePrice
    };
  });

  res.json({ refreshed });
});

function getBigPlayersUniverseData(budget = 100000, parts = 5) {
  const ticks = getLiveTicks();
  const stocks = Object.values(ticks);
  const results = [];
  const nowStr = formatTimestampWithMs();

  for (const s of stocks) {
    const sym = s.symbol || s.tradingSymbol;
    const livePrice = Number(s.price || s.ltp || 0);
    if (!livePrice) continue;

    const high915 = Number(s.high915 || livePrice);
    const low915 = Number(s.low915 || livePrice);
    const todayLow = Number(s.today_low || low915);
    const todayHigh = Number(s.today_high || high915);
    const broke915Low = Boolean(todayLow < low915);
    const pullbackInside915 = Boolean(broke915Low && livePrice >= low915 && livePrice <= high915);
    const liveChg = Number(s.change_pct || 0);
    const maxQty = computeMaxQty(budget, parts, livePrice);
    const sl = Number((livePrice * 0.99).toFixed(2));
    const riskRs = Math.round((livePrice - sl) * maxQty);

    const breakout = livePrice >= (high915 * 0.995)
      ? 'Confirmed'
      : (livePrice >= low915 ? 'Inside 9:15' : 'Below 9:15');

    results.push({
      Symbol: sym,
      symbol: sym,
      Price: livePrice,
      price: livePrice,
      'Last Update': nowStr,
      'Time Log': nowStr,
      last_update: nowStr,
      time_log: nowStr,
      timestamp: nowStr,
      'CHG%': liveChg,
      change_pct: liveChg,
      '9:15 High': high915,
      '9:15 Low': low915,
      'Today Low': todayLow,
      'Today High': todayHigh,
      'New Low': broke915Low ? 'Yes' : 'No',
      'Pullback (9:15)': pullbackInside915 ? 'Inside 9:15' : '—',
      Breakout: breakout,
      SupportPrice: low915,
      support_price: low915,
      EntryPrice: livePrice,
      SL: sl,
      sl: sl,
      MaxQty: maxQty,
      maxQty: maxQty,
      RiskRs: riskRs,
      TodayLow: todayLow,
      TodayHigh: todayHigh,
      low915: low915,
      high915: high915,
      vol915: Number(s.volume || 100000),
      new_low_formed: broke915Low,
      broke_915_low: broke915Low,
      pullback_inside_915: pullbackInside915,
      pullback: pullbackInside915
    });
  }

  return results;
}

app.get(['/api/strategies/bigplayers', '/api/strategies/bigplayers/refresh'], async (req, res) => {
  const budget = parseFloat(req.query.budget) || 100000;
  const parts = parseFloat(req.query.parts) || 5;
  const newLowToggle = req.query.new_low === 'true' || req.query.newlow === 'true' || req.query.broke_915_low === 'true';
  const pullbackToggle = req.query.pullback === 'true' || req.query.pullback_inside_915 === 'true';
  const tillTime = req.query.till_time || req.query.time || req.query.time_cutoff || null;

  let data = getBigPlayersUniverseData(budget, parts);

  // Apply filters
  if (pullbackToggle) {
    data = data.filter(item => item.pullback_inside_915 === true);
  } else if (newLowToggle && !tillTime) {
    data = data.filter(item => item.broke_915_low === true || item.new_low_formed === true);
  }

  // Sort descending by CHG%
  data.sort((a, b) => (parseFloat(b['CHG%']) || 0) - (parseFloat(a['CHG%']) || 0));

  res.json({
    strategy: 'bigplayers',
    name: 'Big Players',
    count: data.length,
    data: data,
    columns: BIG_PLAYERS_COLUMNS,
    source: 'Market Universe Store',
    filter_new_low: newLowToggle,
    filter_pullback: pullbackToggle,
    date: getTodayISTDate()
  });
});

app.post('/api/strategies/bigplayers/qty', async (req, res) => {
  const { budget, parts } = req.body || {};
  const b = parseFloat(budget) || 100000;
  const p = parseFloat(parts) || 5;

  const data = getBigPlayersUniverseData(b, p).map(item => ({
    Symbol: item.Symbol,
    MaxQty: item.MaxQty
  }));

  res.json({ data });
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
app.get('/api/broker/status', (req, res) => {
  res.json({
    connected: brokerConnected,
    broker: activeBroker,
    client_id_masked: activeBroker === 'angel' ? 'IIR***71' : '110***34',
    timestamp: new Date().toISOString()
  });
});

app.post('/api/broker/connect', async (req, res) => {
  const { broker, api_key, client_id, password, totp_secret } = req.body || {};
  if (broker === 'angel' || broker === 'dhan') {
    activeBroker = broker;
    brokerConnected = true;
    return res.json({
      connected: true,
      broker: activeBroker,
      client_id_masked: client_id ? `${client_id.slice(0, 3)}***${client_id.slice(-2)}` : 'ACTIVE',
      message: `Connected successfully to ${broker === 'angel' ? 'Angel One' : 'Dhan'}`
    });
  }
  res.status(400).json({ connected: false, detail: 'Invalid broker specified' });
});

app.post('/api/broker/disconnect', (req, res) => {
  brokerConnected = false;
  res.json({
    connected: false,
    message: 'Broker disconnected successfully'
  });
});

app.get('/api/broker/angel/ws-status', (req, res) => {
  res.json({
    connected: brokerConnected,
    active_broker: activeBroker,
    broker_connected: brokerConnected
  });
});

app.get('/api/broker/angel/ticks', (req, res) => {
  const ticks = getLiveTicks();
  res.json({
    broker: activeBroker,
    count: Object.keys(ticks).length,
    timestamp: formatTimestampWithMs(),
    ticks: ticks
  });
});

app.get('/api/tokens/watchlist', (req, res) => {
  const ticks = getLiveTicks();
  const list = Object.values(ticks);
  res.json({ 
    success: true, 
    total: list.length, 
    connected: brokerConnected,
    data: list 
  });
});

app.get('/api/health', (req, res) => {
  res.json({ status: 'healthy', timestamp: new Date().toISOString(), source: 'Direct Broker Stream Engine' });
});

app.get('/api', (req, res) => {
  res.json({
    status: 'ok',
    message: 'TradeAlgo Pro Strategy API',
    source: 'Direct Broker Stream Engine',
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

app.get('/style.css', (req, res) => {
  res.sendFile(path.join(__dirname, 'style.css'));
});

app.get(['/login.html', '/auth/login.html', '/login'], (req, res) => {
  res.sendFile(path.join(__dirname, 'auth', 'login.html'));
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
