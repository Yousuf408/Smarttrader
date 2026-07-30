// ================================================================
// BIGPLAYERS.JS - Big Players Strategy (Standalone)
// ================================================================

// ================================================================
// SECTION 1: STATE VARIABLES
// ================================================================

let lastBigPlayersData = null;
let bigPlayersAutoTimer = null;
const BIG_PLAYERS_REFRESH_MS = 30000;

// ================================================================
// SECTION 2: AUTO-BUY STATE + CONFIGURATION (Big Players Specific)
// ================================================================

let _bpAutoBuyEnabled = false;
const _bpBoughtSymbols = new Set();

const BIG_PLAYERS_AUTO_BUY = {
    requireBreakoutActive: true,
    maxStocksPerDay: 5,
    requirePriceAboveSupport: true,
    minVolumeSpike: 2.0,
    stopLossPercent: 2.0,
    targetPercent: 5.0,
};

// ================================================================
// SECTION 3: FETCH BIG PLAYERS DATA
// ================================================================

async function fetchBigPlayers() {
    try {
        const url = '/api/strategies/bigplayers';
        const response = await fetch(url);
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        const result = await response.json();
        lastBigPlayersData = result;
        return result;
    } catch (error) {
        console.error('Error fetching Big Players:', error);
        showToast('⚠️ Error', 'Failed to fetch Big Players data');
        return null;
    }
}

// ================================================================
// SECTION 5: RENDER BIG PLAYERS TABLE
// ================================================================

function renderBigPlayersData(result) {
    const strategy = STRATEGIES['bigplayers'];
    const data = result.data || [];
    const columns = [...strategy.columns];
    columns.push('Action');

    const thead = document.querySelector('#screenerHead tr');
    thead.innerHTML = columns.map(col => `<th>${col}</th>`).join('');

    const tbody = document.getElementById('screenerBody');
    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--text-muted);">No stocks found for Big Players strategy.</td></tr>`;
    } else {
        tbody.innerHTML = data.map(row => {
            const values = columns.map(col => {
                if (col === 'Action') return '';

                if (col === 'Price') return `₹${row.price || row.Price || ''}`;

                if (col === 'CHG%') {
                    // The API key is "CHG%", but refresh/ticks set row.chg / row.CHG.
                    let chg = row.chg ?? row.CHG ?? row['CHG%'] ?? row.change ?? '';
                    if (chg !== '' && !isNaN(parseFloat(chg))) {
                        const v = parseFloat(chg);
                        const sign = v > 0 ? '+' : '';
                        const color = v >= 0 ? 'var(--color-green, #22c55e)' : 'var(--color-red, #ef4444)';
                        return `<span style="color:${color};font-weight:600;">${sign}${v.toFixed(2)}%</span>`;
                    }
                    return `<span style="color:var(--text-muted);">-</span>`;
                }

                if (col === 'Breakout') {
                    const status = row.breakout || row.Breakout || 'Waiting';
                    const color = status === 'Active' ? 'var(--color-success)' : 'var(--color-warning)';
                    return `<span style="color:${color};font-weight:700;">${status}</span>`;
                }

                if (col === 'Support Price') {
                    return `₹${row.supportPrice || row.support || row.SupportPrice || ''}`;
                }

                if (col === 'MaxQty') {
                    return row.maxQty || row.MaxQty || '';
                }

                if (col === 'Symbol') {
                    return `<strong>${row.Symbol || row.symbol || ''}</strong>`;
                }

                return row[col] || row[col.toLowerCase()] || row[col.toUpperCase()] || '';
            });

            const symbol = row.Symbol || row.symbol || 'Unknown';
            const isAutoBuyEnabled = window.autoBuyEnabled || false;

            return `<tr>
                ${values.filter(v => v !== '').map(val => `<td>${val}</td>`).join('')}
                <td>
                    <button class="btn-place-order btn-sm" onclick="placeOrder('${symbol}')" ${isAutoBuyEnabled ? 'disabled' : ''}>
                        Place Order
                    </button>
                </td>
            </tr>`;
        }).join('');
    }

    document.getElementById('screenerCount').textContent = `Showing ${data.length} stocks`;

    if (typeof updatePlaceOrderButtons === 'function') {
        updatePlaceOrderButtons();
    }
}

// ================================================================
// SECTION 6: BIG PLAYERS REFRESH (with red_filter)
// ================================================================

async function fetchBigPlayersRefresh(silent = true) {
    if (!lastBigPlayersData || !lastBigPlayersData.data || lastBigPlayersData.data.length === 0) {
        return;
    }

    const symbols = lastBigPlayersData.data.map(r => r.Symbol || r.symbol).filter(Boolean);
    if (symbols.length === 0) return;

    try {
        const url = `/api/strategies/bigplayers/refresh?tickers=${encodeURIComponent(symbols.join(','))}`;
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) return;

        const result = await response.json();

        const bySymbol = {};
        for (const r of (result.refreshed || [])) {
            bySymbol[r.Symbol || r.symbol] = r;
        }

        let touched = 0;
        for (const row of lastBigPlayersData.data) {
            const symbol = row.Symbol || row.symbol;
            const updated = bySymbol[symbol];
            if (!updated) continue;

            if (typeof updated.Price === 'number') row.price = updated.Price;
            // API returns CHG% (with percent sign in the key)
            if (updated['CHG%'] != null) {
                row.chg = parseFloat(updated['CHG%']);
            }
            if (typeof updated.Breakout === 'string') row.breakout = updated.Breakout;
            if (typeof updated.SupportPrice === 'number') row.supportPrice = updated.SupportPrice;
            if (updated.TodayLow != null) {
                row.TodayLow = parseFloat(updated.TodayLow);
            }
            if (updated.low915 != null) {
                row.low915 = parseFloat(updated.low915);
            }
            if (updated.high915 != null) {
                row.high915 = parseFloat(updated.high915);
            }
            touched++;
        }

        if (touched > 0) {
            const activePage = document.querySelector('.page.active');
            const onScreener = activePage && activePage.id === 'page-screener';
            const strategyId = document.getElementById('strategySelect')?.value;
            const isBigPlayers = strategyId === 'bigplayers';

            if (onScreener && isBigPlayers) {
                renderBigPlayersData(lastBigPlayersData);
                if (!silent) showToast('🔄 Refreshed', `${touched} Big Players stocks updated`);
            }

        }
    } catch (e) {
        console.error('Big Players refresh failed:', e);
    }
}

// ================================================================
// SECTION 7: START/STOP AUTO REFRESH
// ================================================================

// ── Big Players' own EventSource (no dependency on screener.js) ──
let _bpEventSource = null;

function _bpApplyTicks(ticks) {
    const rows = document.querySelectorAll('#screenerBody tr');
    if (!rows.length) return;
    const headers = Array.from(document.querySelectorAll('#screenerHead th'));
    const priceIdx = headers.findIndex(h => h.textContent.trim() === 'Price');
    const chgIdx   = headers.findIndex(h => h.textContent.trim() === 'CHG%');
    if (priceIdx < 0) return;

    for (const tr of rows) {
        const cells = tr.querySelectorAll('td');
        const symEl = cells[0];
        if (!symEl) continue;
        const sym = symEl.textContent.trim();
        const tick = ticks[sym];
        if (!tick) continue;

        // Price
        if (tick.ltp != null && priceIdx < cells.length) {
            cells[priceIdx].textContent = `₹${Number(tick.ltp).toFixed(2)}`;
            cells[priceIdx].style.transition = 'background 0.15s';
            cells[priceIdx].style.background = 'rgba(34,197,94,0.15)';
            setTimeout(() => { cells[priceIdx].style.background = ''; }, 300);
        }
        // CHG%
        if (tick.change_pct != null && chgIdx >= 0 && chgIdx < cells.length) {
            const v = Number(tick.change_pct);
            const sign = v > 0 ? '+' : '';
            cells[chgIdx].textContent = `${sign}${v.toFixed(2)}%`;
            cells[chgIdx].style.color = v >= 0 ? 'var(--color-green, #22c55e)' : 'var(--color-red, #ef4444)';
        }
        // Also update in-memory data
        if (lastBigPlayersData && lastBigPlayersData.data) {
            const row = lastBigPlayersData.data.find(r => (r.Symbol || r.symbol) === sym);
            if (row) {
                if (tick.ltp != null) {
                    row.Price = Number(tick.ltp);
                    row.price = Number(tick.ltp);
                }
                if (tick.change_pct != null) {
                    row.CHG = Number(tick.change_pct);
                    row.chg = Number(tick.change_pct);
                }
            }
        }

        // ── Auto-buy on every tick (no 30s wait) ──
        if (lastBigPlayersData && lastBigPlayersData.data && _bpAutoBuyEnabled) {
            const row = lastBigPlayersData.data.find(r => (r.Symbol || r.symbol) === sym);
            if (!row) continue;
            if (_bpBoughtSymbols.has(sym)) continue;

            const low915 = parseFloat(row.low915);
            const high915 = parseFloat(row.high915);
            const todayLow = parseFloat(row.TodayLow);
            const price = parseFloat(row.Price ?? row.price);
            const maxQty = parseInt(row.maxQty || row.MaxQty || 0);

            // Must have candle data, created a new low, has margin
            if (!Number.isFinite(low915) || !Number.isFinite(high915) || low915 <= 0 || high915 <= 0) continue;
            if (maxQty <= 0) continue;
            if (!Number.isFinite(todayLow) || todayLow >= low915) continue;

            // Condition 3: price recovered to ≥ 75% of candle range above low
            const range = high915 - low915;
            if (range <= 0) continue;
            if (!Number.isFinite(price) || price < low915 + range * 0.75) continue;

            // All conditions met — buy immediately
            _bpBoughtSymbols.add(sym);
            _placeBpOrder(sym, maxQty);
        }
    }
}

function _startBigPlayersTicks() {
    _stopBigPlayersTicks();
    _bpEventSource = new EventSource('/api/market/bigplayers-ticks/stream');
    _bpEventSource.onmessage = function (ev) {
        try {
            const data = JSON.parse(ev.data);
            if (data.connected && data.ticks) {
                const activePage = document.querySelector('.page.active');
                const onScreener = activePage && activePage.id === 'page-screener';
                if (onScreener) {
                    _bpApplyTicks(data.ticks);
                }
            }
        } catch (_) {}
    };
    _bpEventSource.onerror = function () {};
}

function _stopBigPlayersTicks() {
    if (_bpEventSource) {
        _bpEventSource.close();
        _bpEventSource = null;
    }
}

function startBigPlayersAutoRefresh() {
    stopBigPlayersAutoRefresh();
    bigPlayersAutoTimer = setInterval(() => {
        fetchBigPlayersRefresh(true);
    }, BIG_PLAYERS_REFRESH_MS);
    // Big Players has its own dedicated tick stream
    _startBigPlayersTicks();
}

function stopBigPlayersAutoRefresh() {
    if (bigPlayersAutoTimer) {
        clearInterval(bigPlayersAutoTimer);
        bigPlayersAutoTimer = null;
    }
    _stopBigPlayersTicks();
}

// ================================================================
// SECTION 8: BIG PLAYERS AUTO BUY (tick-triggered via _bpApplyTicks)
// ================================================================

/** Place a single Big Players buy order immediately via the backend. */
async function _placeBpOrder(symbol, quantity) {
    try {
        const response = await fetch('/api/orders/place-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                orders: [{
                    symbol,
                    quantity,
                    transactionType: 'BUY',
                    productType: 'INTRADAY',
                    afterMarketOrder: window.amoEnabled || false,
                    amoTime: 'OPEN',
                }],
                productType: 'INTRADAY',
                afterMarketOrder: window.amoEnabled || false,
                amoTime: 'OPEN',
                source: 'bigplayers_tick_auto_buy',
                strategy: 'Big Players',
            }),
        });
        const result = await response.json();
        if (response.ok && result.succeeded > 0) {
            showToast('✅ BP Buy', `${symbol} × ${quantity} at 75% recovery`);
        } else {
            const err = (result.results && result.results[0] && result.results[0].error) || result.detail || 'Unknown';
            showToast('❌ BP Buy Failed', `${symbol}: ${err}`);
            console.error('BP auto-buy order failed:', symbol, err);
        }
    } catch (e) {
        showToast('❌ BP Buy Error', `${symbol}: ${e.message}`);
    }
}

async function autoBuyAllStocksBigPlayers() {
    if (!lastBigPlayersData || !Array.isArray(lastBigPlayersData.data) || lastBigPlayersData.data.length === 0) {
        showToast('⚠️ No Stocks', 'Run screener first to load Big Players stocks');
        return;
    }

    let eligibleStocks = lastBigPlayersData.data.filter(row => {
        const low915 = parseFloat(row.low915);
        const high915 = parseFloat(row.high915);
        const todayLow = parseFloat(row.TodayLow);
        const price = parseFloat(row.price || row.Price || 0);
        const maxQty = parseInt(row.maxQty || row.MaxQty || 0);

        // Must have candle data + margin
        if (!Number.isFinite(low915) || !Number.isFinite(high915) || low915 <= 0 || high915 <= 0) return false;
        if (maxQty <= 0) return false;

        // Condition 2: stock must have created a new low (broke below low915)
        if (!Number.isFinite(todayLow) || todayLow >= low915) return false;

        // Condition 3: price must have recovered to ≥ 75% of the candle's range above the low
        const range = high915 - low915;
        if (range <= 0) return false;
        const entryPrice = low915 + range * 0.75;

        return price >= entryPrice;
    });

    if (eligibleStocks.length === 0) {
        showToast('⚠️ No 75% Recovery', 'No stocks have recovered to 75% of candle range after new low');
        return;
    }

    eligibleStocks.sort((a, b) => {
        const aChg = parseFloat(a.chg || a.CHG || 0) || 0;
        const bChg = parseFloat(b.chg || b.CHG || 0) || 0;
        return bChg - aChg;
    });

    const topN = eligibleStocks.slice(0, BIG_PLAYERS_AUTO_BUY.maxStocksPerDay);

    const orders = topN.map(r => ({
        symbol: r.Symbol || r.symbol,
        quantity: parseInt(r.maxQty || r.MaxQty || 0, 10),
        transactionType: 'BUY',
        productType: 'INTRADAY',
        afterMarketOrder: window.amoEnabled || false,
        amoTime: 'OPEN',
        breakoutStatus: r.breakout || r.Breakout,
        supportPrice: r.supportPrice || r.SupportPrice,
    }));

    const entryDetails = topN.map(r => {
        const lo = parseFloat(r.low915);
        const hi = parseFloat(r.high915);
        const ep = lo + (hi - lo) * 0.75;
        return `${r.Symbol}@₹${ep.toFixed(2)}`;
    }).join(', ');
    showToast('🏢 BP Auto-Buy', `Submitting ${orders.length}: ${entryDetails}`);

    try {
        const response = await fetch('/api/orders/place-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                orders,
                productType: 'INTRADAY',
                afterMarketOrder: window.amoEnabled || false,
                amoTime: 'OPEN',
                source: 'bigplayers_auto_buy',
                strategy: 'Big Players',
            }),
        });

        const result = await response.json();

        if (response.ok) {
            const succeeded = result.succeeded || 0;
            const total = result.total || orders.length;
            if (succeeded === total) {
                showToast('✅ Auto-Buy Complete', `${succeeded}/${total} Big Players orders placed`);
            } else if (succeeded > 0) {
                showToast('⚠️ Partial Success', `${succeeded}/${total} orders placed. Check console for details.`);
            } else {
                showToast('❌ Auto-Buy Failed', `0/${total} orders placed. See console.`);
            }
        } else {
            showToast('❌ Auto-Buy Failed', result.detail || `HTTP ${response.status}`);
        }
    } catch (e) {
        showToast('❌ Network Error', e.message || 'Request failed');
        console.error('Big Players auto-buy error:', e);
    }
}

// ================================================================
// SECTION 9: BIG PLAYERS BREAKOUT LOGIC (placeholder)
// ================================================================

function calculateBreakoutStatus(row) {
    const price = parseFloat(row.price || row.Price || 0);
    const support = parseFloat(row.supportPrice || row.SupportPrice || 0);
    const resistance = parseFloat(row.resistancePrice || row.ResistancePrice || 0);
    const prevHigh = parseFloat(row.prevHigh || row.PrevHigh || 0);

    if (price > prevHigh && price > support) {
        return 'Active';
    }
    return 'Waiting';
}

// ================================================================
// SECTION 10: EXPOSE GLOBALLY
// ================================================================

window.fetchBigPlayers = fetchBigPlayers;
window.renderBigPlayersData = renderBigPlayersData;
window.fetchBigPlayersRefresh = fetchBigPlayersRefresh;
window.startBigPlayersAutoRefresh = startBigPlayersAutoRefresh;
window.stopBigPlayersAutoRefresh = stopBigPlayersAutoRefresh;
window.autoBuyAllStocksBigPlayers = autoBuyAllStocksBigPlayers;
window.calculateBreakoutStatus = calculateBreakoutStatus;
window.lastBigPlayersData = lastBigPlayersData;
window.toggleBpAutoBuy = toggleBpAutoBuy;
window._bpAutoBuyEnabled = _bpAutoBuyEnabled;

// ================================================================
// SECTION 11: NEW LOW ONLY TOGGLE
// ================================================================

let _newLowOnly = false;

function onNewLowToggle() {
    _newLowOnly = document.getElementById('newLowToggle')?.checked || false;
    document.getElementById('newLowStatus').textContent = _newLowOnly ? 'ON' : 'OFF';
    if (lastBigPlayersData) {
        renderBigPlayersData(lastBigPlayersData);
    }
}

/** Filter the data array when New Low Only is active.
 *  Condition 2 of the Big Players strategy: shows only stocks that have
 *  ALREADY created a new low today — meaning the day's lowest price so far
 *  (TodayLow) is BELOW the 9:15 opening candle's low (low915).
 *  The stock has broken below the opening candle range, creating a new low. */
function _applyNewLowFilter(data) {
    if (!_newLowOnly || !data) return data;
    return data.filter(r => {
        const todayLow = parseFloat(r.TodayLow || r.todayLow || 0);
        const low915 = parseFloat(r.low915 || 0);
        return todayLow < low915 && todayLow > 0 && low915 > 0;
    });
}

/** Toggle Big Players auto-buy ON/OFF. */
function toggleBpAutoBuy() {
    _bpAutoBuyEnabled = document.getElementById('bpAutoBuyToggle')?.checked || false;
    document.getElementById('bpAutoBuyStatus').textContent = _bpAutoBuyEnabled ? 'ON' : 'OFF';
    if (_bpAutoBuyEnabled) {
        _bpBoughtSymbols.clear();  // Reset so all stocks are eligible again
        showToast('🏢 BP Auto Buy ON', 'Watching live ticks for 75% recovery entry');
        if (!lastBigPlayersData || !Array.isArray(lastBigPlayersData.data) || lastBigPlayersData.data.length === 0) {
            showToast('⚠️ Run Screener First', 'Click Refresh to load stocks before auto-buy.');
        }
    } else {
        showToast('👤 BP Manual Mode', 'Big Players auto-buy disabled');
    }
}

// Wrap renderBigPlayersData with new-low filtering
const _origRenderBp = renderBigPlayersData;
renderBigPlayersData = function (result) {
    const filtered = {...result};
    filtered.data = _applyNewLowFilter(result.data || []);
    _origRenderBp(filtered);
};

// ================================================================
// SECTION 12: INIT
// ================================================================

// (auto‑refresh is started only when the user selects the Big Players
//  strategy via the strategy select handler)

console.log('🏢 Big Players strategy module loaded!');
console.log('📌 Features:');
console.log('  - Fetch Big Players data from backend');
console.log('  - Auto-refresh every 30 seconds (optional)');
console.log('  - Auto-buy only Active breakout stocks');
console.log('  - 🔴 Stocks must have RED 9:15 candle (close < open)');