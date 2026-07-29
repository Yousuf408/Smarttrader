// ================================================================
// BIGPLAYERS.JS - Big Players Strategy (Standalone)
// ================================================================

// ================================================================
// SECTION 1: STATE VARIABLES
// ================================================================

let lastBigPlayersData = null;
let bigPlayersAutoTimer = null;
const BIG_PLAYERS_REFRESH_MS = 30000;

// ---- Red filter state ----
let currentRedFilter = true;   // default: only red candles

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
// SECTION 3: DYNAMICALLY ADD RED FILTER CHECKBOX
// ================================================================

function addRedFilterCheckbox() {
    // Check if already added to avoid duplicates
    if (document.getElementById('redFilterCheckbox')) return;

    // Find a suitable container – try common IDs
    let container = document.getElementById('screenerControls') ||
                    document.getElementById('strategyControls') ||
                    document.querySelector('#page-screener .controls') ||
                    document.querySelector('#page-screener') ||
                    document.body;

    // Create label and checkbox
    const label = document.createElement('label');
    label.style.marginLeft = '20px';
    label.style.fontSize = '14px';
    label.style.display = 'inline-flex';
    label.style.alignItems = 'center';
    label.style.gap = '6px';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = 'redFilterCheckbox';
    checkbox.checked = true;   // default: enabled
    checkbox.addEventListener('change', toggleRedFilter);

    const text = document.createTextNode(' Only Red First Candle (close < open)');

    label.appendChild(checkbox);
    label.appendChild(text);

    // Insert before the first child or at the end
    container.insertBefore(label, container.firstChild);
}

// ================================================================
// SECTION 4: FETCH BIG PLAYERS DATA (with red_filter)
// ================================================================

async function fetchBigPlayers(redFilter) {
    // Read checkbox if not passed explicitly
    if (redFilter === undefined) {
        const checkbox = document.getElementById('redFilterCheckbox');
        redFilter = checkbox ? checkbox.checked : true;
    }
    currentRedFilter = redFilter;

    try {
        const url = `/api/strategies/bigplayers?red_filter=${redFilter}`;
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

async function fetchBigPlayersRefresh(silent = true, redFilter) {
    if (!lastBigPlayersData || !lastBigPlayersData.data || lastBigPlayersData.data.length === 0) {
        return;
    }

    const symbols = lastBigPlayersData.data.map(r => r.Symbol || r.symbol).filter(Boolean);
    if (symbols.length === 0) return;

    if (redFilter === undefined) {
        const checkbox = document.getElementById('redFilterCheckbox');
        redFilter = checkbox ? checkbox.checked : currentRedFilter;
    }
    currentRedFilter = redFilter;

    try {
        const url = `/api/strategies/bigplayers/refresh?tickers=${encodeURIComponent(symbols.join(','))}&red_filter=${redFilter}`;
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
// SECTION 7: START/STOP AUTO REFRESH
// ================================================================

function startBigPlayersAutoRefresh() {
    stopBigPlayersAutoRefresh();
    bigPlayersAutoTimer = setInterval(() => {
        fetchBigPlayersRefresh(true);
    }, BIG_PLAYERS_REFRESH_MS);
    // Also kick off live tick polling so Price/CHG% update tick-by-tick
    if (window.startLiveTickPoll) window.startLiveTickPoll();
}

function stopBigPlayersAutoRefresh() {
    if (bigPlayersAutoTimer) {
        clearInterval(bigPlayersAutoTimer);
        bigPlayersAutoTimer = null;
    }
    if (window.stopLiveTickPoll) window.stopLiveTickPoll();
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
// SECTION 10: TOGGLE RED FILTER (called when checkbox changes)
// ================================================================

function toggleRedFilter() {
    const checkbox = document.getElementById('redFilterCheckbox');
    if (!checkbox) return;
    const redFilter = checkbox.checked;
    currentRedFilter = redFilter;
    // Re‑fetch data with the new filter
    fetchBigPlayers(redFilter).then(result => {
        if (result) {
            lastBigPlayersData = result;
            renderBigPlayersData(result);
        }
    });
}

// ================================================================
// SECTION 11: EXPOSE GLOBALLY
// ================================================================

window.fetchBigPlayers = fetchBigPlayers;
window.renderBigPlayersData = renderBigPlayersData;
window.fetchBigPlayersRefresh = fetchBigPlayersRefresh;
window.startBigPlayersAutoRefresh = startBigPlayersAutoRefresh;
window.stopBigPlayersAutoRefresh = stopBigPlayersAutoRefresh;
window.autoBuyAllStocksBigPlayers = autoBuyAllStocksBigPlayers;
window.calculateBreakoutStatus = calculateBreakoutStatus;
window.toggleRedFilter = toggleRedFilter;
window.lastBigPlayersData = lastBigPlayersData;

// ================================================================
// SECTION 12: INIT – Add checkbox and start auto‑refresh
// ================================================================

// Add the red‑filter checkbox to the page
addRedFilterCheckbox();

// Optionally, start auto‑refresh when the script loads
// (you may want to call this only when the Big Players strategy is selected)
// startBigPlayersAutoRefresh();

console.log('🏢 Big Players strategy module loaded!');
console.log('📌 Features:');
console.log('  - Fetch Big Players data from backend');
console.log('  - Auto-refresh every 30 seconds (optional)');
console.log('  - Auto-buy only Active breakout stocks');
console.log('  - 🟥 Red‑candle filter toggle (checkbox injected)');