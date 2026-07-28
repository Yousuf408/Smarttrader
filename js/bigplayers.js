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
    // Only buy stocks with 'Active' breakout status
    requireBreakoutActive: true,

    // Maximum stocks to auto-buy per day
    maxStocksPerDay: 5,

    // Price must be above support level
    requirePriceAboveSupport: true,

    // Minimum volume spike (2x = 2.0)
    minVolumeSpike: 2.0,

    // Stop-loss percentage
    stopLossPercent: 2.0,

    // Target percentage
    targetPercent: 5.0,
};

// ================================================================
// SECTION 3: FETCH BIG PLAYERS DATA
// ================================================================

async function fetchBigPlayers() {
    try {
        const response = await fetch('/api/strategies/bigplayers');
        if (!response.ok) throw new Error(`API returned ${response.status}`);
        const result = await response.json();
        return result;
    } catch (error) {
        console.error('Error fetching Big Players:', error);
        showToast('⚠️ Error', 'Failed to fetch Big Players data');
        return null;
    }
}

// ================================================================
// SECTION 4: RENDER BIG PLAYERS TABLE
// ================================================================

function renderBigPlayersData(result) {
    const strategy = STRATEGIES['bigplayers'];
    const data = result.data || [];
    const columns = [...strategy.columns];
    columns.push('Action');

    // Update table headers
    const thead = document.querySelector('#screenerHead tr');
    thead.innerHTML = columns.map(col => `<th>${col}</th>`).join('');

    // Update table body
    const tbody = document.getElementById('screenerBody');
    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--text-muted);">No stocks found for Big Players strategy.</td></tr>`;
    } else {
        tbody.innerHTML = data.map(row => {
            const values = columns.map(col => {
                if (col === 'Action') return '';

                if (col === 'Price') return `₹${row.price || row.Price || ''}`;

                if (col === 'CHG%') {
                    const chg = row.chg || row.CHG || row.change || '';
                    const isPositive = String(chg).includes('+') || (parseFloat(chg) > 0);
                    const color = isPositive ? 'var(--color-success)' : 'var(--color-danger)';
                    return `<span style="color:${color};font-weight:600;">${chg}</span>`;
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

                // Fallback for any other columns
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

    // Update place order buttons
    if (typeof updatePlaceOrderButtons === 'function') {
        updatePlaceOrderButtons();
    }
}

// ================================================================
// SECTION 5: BIG PLAYERS REFRESH
// ================================================================

async function fetchBigPlayersRefresh(silent = true) {
    if (!lastBigPlayersData || !lastBigPlayersData.data || lastBigPlayersData.data.length === 0) {
        return;
    }

    const symbols = lastBigPlayersData.data.map(r => r.Symbol || r.symbol).filter(Boolean);
    if (symbols.length === 0) return;

    try {
        const response = await fetch(
            `/api/strategies/bigplayers/refresh?tickers=${encodeURIComponent(symbols.join(','))}`,
            { cache: 'no-store' }
        );
        if (!response.ok) return;

        const result = await response.json();

        // Update in-memory data
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
            if (typeof updated.CHG === 'number' || typeof updated.CHG === 'string') row.chg = updated.CHG;
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
// SECTION 6: START/STOP AUTO REFRESH
// ================================================================

function startBigPlayersAutoRefresh() {
    stopBigPlayersAutoRefresh();
    bigPlayersAutoTimer = setInterval(() => {
        fetchBigPlayersRefresh(true);
    }, BIG_PLAYERS_REFRESH_MS);
}

function stopBigPlayersAutoRefresh() {
    if (bigPlayersAutoTimer) {
        clearInterval(bigPlayersAutoTimer);
        bigPlayersAutoTimer = null;
    }
}

// ================================================================
// SECTION 7: BIG PLAYERS AUTO BUY (Different from ORB)
// ================================================================

async function autoBuyAllStocksBigPlayers() {
    // Check if data exists
    if (!lastBigPlayersData || !Array.isArray(lastBigPlayersData.data) || lastBigPlayersData.data.length === 0) {
        showToast('⚠️ No Stocks', 'Run screener first to load Big Players stocks');
        return;
    }

    // Step 1: Filter - Only 'Active' Breakout stocks with MaxQty > 0
    let eligibleStocks = lastBigPlayersData.data.filter(row => {
        const breakout = row.breakout || row.Breakout || 'Waiting';
        const maxQty = parseInt(row.maxQty || row.MaxQty || 0);
        const isActive = breakout === 'Active';
        const hasMargin = maxQty > 0;

        // If we require price above support
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

    // Step 2: Sort by CHG% descending (strongest momentum first)
    eligibleStocks.sort((a, b) => {
        const aChg = parseFloat(a.chg || a.CHG || 0) || 0;
        const bChg = parseFloat(b.chg || b.CHG || 0) || 0;
        return bChg - aChg;
    });

    // Step 3: Cap at max stocks per day
    const topN = eligibleStocks.slice(0, BIG_PLAYERS_AUTO_BUY.maxStocksPerDay);

    // Step 4: Build orders
    const orders = topN.map(r => ({
        symbol: r.Symbol || r.symbol,
        quantity: parseInt(r.maxQty || r.MaxQty || 0, 10),
        transactionType: 'BUY',
        productType: 'INTRADAY',
        afterMarketOrder: window.amoEnabled || false,
        amoTime: 'OPEN',
        // Big Players specific metadata
        breakoutStatus: r.breakout || r.Breakout,
        supportPrice: r.supportPrice || r.SupportPrice,
    }));

    // Step 5: Show confirmation
    showToast(
        '🏢 Big Players Auto-Buy',
        `Submitting ${orders.length} stock(s) with Active breakout`
    );

    // Step 6: Submit orders to backend
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
// SECTION 8: BIG PLAYERS BREAKOUT LOGIC (To be implemented)
// ================================================================

/**
 * Calculate Breakout status for a stock
 * 
 * @param {Object} row - Stock data row
 * @param {number} row.price - Current price
 * @param {number} row.prevHigh - Previous day's high
 * @param {number} row.supportPrice - Support price level
 * @param {number} row.resistancePrice - Resistance price level
 * @returns {string} 'Active' or 'Waiting'
 */
function calculateBreakoutStatus(row) {
    // ⚠️ You'll define this logic later
    // This is a placeholder until you explain the exact logic

    const price = parseFloat(row.price || row.Price || 0);
    const support = parseFloat(row.supportPrice || row.SupportPrice || 0);
    const resistance = parseFloat(row.resistancePrice || row.ResistancePrice || 0);
    const prevHigh = parseFloat(row.prevHigh || row.PrevHigh || 0);

    // Example logic (you'll replace this):
    if (price > prevHigh && price > support) {
        return 'Active';
    }
    return 'Waiting';
}

// ================================================================
// SECTION 9: EXPOSE GLOBALLY
// ================================================================

// Expose functions to window so they can be called from HTML
window.fetchBigPlayers = fetchBigPlayers;
window.renderBigPlayersData = renderBigPlayersData;
window.fetchBigPlayersRefresh = fetchBigPlayersRefresh;
window.startBigPlayersAutoRefresh = startBigPlayersAutoRefresh;
window.stopBigPlayersAutoRefresh = stopBigPlayersAutoRefresh;
window.autoBuyAllStocksBigPlayers = autoBuyAllStocksBigPlayers;
window.calculateBreakoutStatus = calculateBreakoutStatus;
window.lastBigPlayersData = lastBigPlayersData;

// ================================================================
// SECTION 10: INIT
// ================================================================

console.log('🏢 Big Players strategy module loaded!');
console.log('📌 Features:');
console.log('  - Fetch Big Players data from backend');
console.log('  - Auto-refresh every 30 seconds');
console.log('  - Auto-buy only Active breakout stocks');
console.log('  - ⏳ Breakout logic: To be implemented');