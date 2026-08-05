// ================================================================
// GoChart — TradingView-style chart panel for the screener
// Opens when a stock row is clicked: chart on the left, list on the right.
// Datafeed contract modelled on the official GoChart SDK demo (codepen).
// NOTE: production use requires a commercial GoChart license; this uses
// the SDK's public demo build + demo license key.
// ================================================================
(function () {
    'use strict';

    const SDK_LICENSE_KEY = 'demo-550e8400-e29b-41d4-a716-446655440000';
    const LIVE_TICKS_URL = '/api/market/live-ticks';

    let chartInstance = null;
    let currentSymbol = '';
    let currentInterval = '5m';
    let streamTimer = null;

    /** SDK streams a continuous line of ticks; poll the app's live-tick
     *  REST endpoint (same feed the screener table updates from). */
    function resetStream() {
        if (streamTimer) { clearInterval(streamTimer); streamTimer = null; }
    }

    function normalizeResolution(res) {
        if (res && typeof res === 'object') {
            if (res.label) return String(res.label);
            if (res.scale && res.units) {
                if (res.units === 'minutes') return String(res.scale);
                if (res.units === 'hours') return String(res.scale * 60);
                if (res.units === 'days') return res.scale === 1 ? '1D' : res.scale + 'D';
            }
            return '5m';
        }
        return String(res || '5m').trim();
    }

    const datafeed = {
        // resolveSymbol(symbolName, onResolve, onError)
        resolveSymbol(symbolName, onResolve, onError) {
            const full = String(symbolName || '');
            const sym = full.split(':').pop().replace(/\.NS$/i, '').trim().toUpperCase();
            if (!sym) { onError('Missing symbol'); return; }
            onResolve({
                symbol: sym,
                full_name: `NSE:${sym}`,
                description: sym,
                exchange: 'NSE',
                type: 'stock',
                session: '09:15-15:30',
                session_label: 'NSE (09:15-15:30 IST)',
                timezone: 'Asia/Kolkata',
                ticker: sym,
                has_intraday: true,
                intraday_multipliers: ['1', '5', '15', '30', '60', '240'],
                supported_resolutions: ['5', '15', '30', '60', '240', '1D', '1W', '1M'],
                volume_precision: 0,
                pricescale: 100,
                minmov: 1,
                tick_size: 0.05,
                data_status: 'streaming',
                exchange_info: { name: 'NSE', code: 'NSE', zone: 'Asia/Kolkata' },
            });
        },

        // getBars — modern composition-based approach: RETURN the UDF object
        async getBars(symbolInfo, resolution, periodParams) {
            const sym = (symbolInfo && symbolInfo.symbol) || currentSymbol;
            const res = normalizeResolution(resolution);
            try {
                const r = await fetch(`/api/chart/history?symbol=${encodeURIComponent(sym)}&resolution=${encodeURIComponent(res)}`);
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const data = await r.json();
                if (data && data.s === 'ok') return data;
                return data || { s: 'no_data' };
            } catch (err) {
                console.error('[gochart] getBars failed', err);
                return { s: 'error', errmsg: String(err && err.message || err) };
            }
        },

        // Minimal search: not wired to a full instrument API; return nothing
        searchSymbols(userInput, exchange, symbolType, onResult) {
            onResult([]);
        },

        // subscribeBars(symbolInfo, resolution, onRealtimeCallback, subscriberUID, onResetCacheNeededCallback)
        subscribeBars(symbolInfo, resolution, onRealtimeCallback, subscriberUID, onResetCacheNeededCallback) {
            resetStream();
            const sym = (symbolInfo && symbolInfo.symbol) || currentSymbol;
            const poll = async () => {
                try {
                    const r = await fetch(LIVE_TICKS_URL);
                    const j = await r.json();
                    const ticks = (j && j.ticks) || {};
                    const tick = ticks[sym] || ticks[sym + '-EQ'];
                    if (tick && tick.ltp != null) {
                        onRealtimeCallback({
                            time: Math.floor(Date.now() / 1000),
                            price: Number(tick.ltp),
                            volume: Number(tick.volume || 0),
                        });
                    }
                } catch (_) { /* transient — next poll retries */ }
            };
            poll();
            streamTimer = setInterval(poll, 2500);
        },

        unsubscribeBars(subscriberUID) {
            resetStream();
        },

        getMarks(symbolInfo, startDate, endDate, onDataCallback, resolution) {
            onDataCallback([]);
        },
        getTimescaleMarks(symbolInfo, startDate, endDate, onDataCallback, resolution) {
            onDataCallback([]);
        },
    };

    function sdkReady() {
        return !!(window.GoChartingSDK && window.GoChartingSDK.createChart);
    }

    function showChartMessage(msg) {
        const c = document.getElementById('chart-container');
        if (!c) return;
        c.innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted,#9aa4b2);font-size:13px;">${msg}</div>`;
    }

    function destroyChart() {
        resetStream();
        if (chartInstance && typeof chartInstance.remove === 'function') {
            try { chartInstance.remove(); } catch (_) { /* ignore */ }
        }
        chartInstance = null;
    }

    function createChart(symbol, interval) {
        destroyChart();
        const container = document.getElementById('chart-container');
        if (!container) return;
        container.innerHTML = '';
        if (!sdkReady()) {
            showChartMessage('⏳ Loading chart library…');
            const boot = () => {
                if (sdkReady()) createChartInner(container, symbol, interval);
                else setTimeout(boot, 150);
            };
            boot();
            return;
        }
        createChartInner(container, symbol, interval);
    }

    function createChartInner(container, symbol, interval) {
        try {
            chartInstance = window.GoChartingSDK.createChart('#chart-container', {
                symbol: `NSE:${symbol}`,
                interval: interval,
                datafeed: datafeed,
                debugLog: false,
                licenseKey: SDK_LICENSE_KEY,
                theme: 'dark',
                enableTrading: false,
                onReady: (inst) => { chartInstance = inst || chartInstance; },
                onError: (err) => {
                    console.error('[gochart] createChart error', err);
                    showChartMessage('❌ Chart failed to load');
                },
            });
        } catch (err) {
            console.error('[gochart] createChart threw', err);
            showChartMessage('❌ Chart failed to load');
        }
    }

    function selectInterval(int) {
        currentInterval = int;
        document.querySelectorAll('#gochartIntervals .gc-int').forEach((b) => {
            b.classList.toggle('active', b.dataset.int === int);
        });
        if (currentSymbol) createChart(currentSymbol, int);
    }

    function openGoChart(symbol) {
        const sym = String(symbol || '').split(':').pop().replace(/\.NS$/i, '').trim().toUpperCase();
        if (!sym) return;
        currentSymbol = sym;

        const title = document.getElementById('gochartSymbol');
        if (title) title.textContent = sym;
        const panel = document.getElementById('gochartPanel');
        const page = document.getElementById('page-screener');
        if (panel) panel.classList.remove('hidden');
        if (page) page.classList.add('chart-open');

        const active = document.querySelector('#gochartIntervals .gc-int.active');
        currentInterval = (active && active.dataset.int) || '5m';
        createChart(sym, currentInterval);
    }

    function closeGoChart() {
        destroyChart();
        currentSymbol = '';
        const panel = document.getElementById('gochartPanel');
        const page = document.getElementById('page-screener');
        if (panel) panel.classList.add('hidden');
        if (page) page.classList.remove('chart-open');
    }

    // Interval switcher
    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('#gochartIntervals .gc-int') : null;
        if (btn) selectInterval(btn.dataset.int || '5m');
    });

    window.openGoChart = openGoChart;
    window.closeGoChart = closeGoChart;
})();
