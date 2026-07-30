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
// SECTION 2: AUTO-BUY CONFIGURATION (Big Players Specific)
// ================================================================

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
// SECTION 8: BIG PLAYERS AUTO BUY (unchanged)
// ================================================================

async function autoBuyAllStocksBigPlayers() {
    if (!lastBigPlayersData || !Array.isArray(lastBigPlayersData.data) || lastBigPlayersData.data.length === 0) {
        showToast('⚠️ No Stocks', 'Run screener first to load Big Players stocks');
        return;
    }

    let eligibleStocks = lastBigPlayersData.data.filter(row => {
        const breakout = row.breakout || row.Breakout || 'Waiting';
        const maxQty = parseInt(row.maxQty || row.MaxQty || 0);
        const isActive = breakout === 'Active';
        const hasMargin = maxQty > 0;

        let aboveSupport = true;
        if (BIG_PLAYERS_AUTO_BUY.requirePriceAboveSupport) {
            const price = parseFloat(row.price || row.Price || 0);
            const support = parseFloat(row.supportPrice || row.SupportPrice || 0);
            aboveSupport = price > support;
        }

        return isActive && hasMargin && aboveSupport;
    });

    if (eligibleStocks.length === 0) {
        showToast('⚠️ No Active Breakouts', 'No stocks with Active breakout status and sufficient margin');
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

    showToast('🏢 Big Players Auto-Buy', `Submitting ${orders.length} stock(s) with Active breakout`);

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

/** Filter the data array when New Low Only is active. */
function _applyNewLowFilter(data) {
    if (!_newLowOnly || !data) return data;
    return data.filter(r => {
        const price = parseFloat(r.price || r.Price || 0);
        const support = parseFloat(r.supportPrice || r.SupportPrice || 0);
        return price < support && price > 0 && support > 0;
    });
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