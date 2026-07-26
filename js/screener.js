// ================================================================
// SCREENER PAGE
// ================================================================

// ================================================================
// FETCH ADVANCE ORB FROM BACKEND API
// ================================================================
async function fetchAdvanceORB() {
    try {
        const response = await fetch('/api/strategies/advanceorb');
        if (!response.ok) {
            throw new Error(`API returned ${response.status}`);
        }
        const result = await response.json();
        return result;
    } catch (error) {
        console.error('Error fetching Advance ORB:', error);
        showToast('⚠️ Error', 'Failed to fetch stock data from backend');
        return null;
    }
}

// ================================================================
// RENDER STRATEGY DATA (from API)
// ================================================================
function renderStrategyData(result) {
    const strategyId = document.getElementById('strategySelect').value;
    const strategy = STRATEGIES[strategyId];
    if (!strategy) return;

    const data = result.data || [];
    const columns = result.columns || strategy.columns || [];

    // Update table headers
    const thead = document.querySelector('#screenerHead tr');
    const headerColumns = [...columns];
    headerColumns.push('Action');
    thead.innerHTML = headerColumns.map(col => `<th>${col}</th>`).join('');

    // Update table rows
    const tbody = document.getElementById('screenerBody');
    if (data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${headerColumns.length}" style="text-align:center;padding:40px;color:var(--text-muted);">No stocks found for this strategy.</td></tr>`;
    } else {
        tbody.innerHTML = data.map(row => {
            // Build values based on column order
            const values = [];
            headerColumns.forEach(col => {
                if (col === 'Action') return;
                
                // Map column names to row properties
                const colKey = col.replace(/ /g, '').replace(/\//g, '');
                let value = row[col] || row[colKey] || row[col.toLowerCase()] || '';
                
                // Special formatting for price
                if (col === 'Price' && typeof value === 'number') {
                    value = `₹${value.toFixed(2)}`;
                }
                // Special formatting for CHG%
                if (col === 'CHG%' && typeof value === 'number') {
                    value = `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
                }
                // Special formatting for GAP%
                if (col === 'GAP%' && typeof value === 'number') {
                    value = `${value > 0 ? '+' : ''}${value.toFixed(2)}%`;
                }
                
                values.push(value);
            });

            const symbol = row.Symbol || row.symbol || 'Unknown';
            return `<tr>
                ${values.map(val => `<td>${val}</td>`).join('')}
                <td>
                    <button class="btn-place-order btn-sm" onclick="placeOrder('${symbol}')" ${autoBuyEnabled ? 'disabled' : ''}>
                        Place Order
                    </button>
                </td>
            </tr>`;
        }).join('');
    }

    document.getElementById('screenerCount').textContent = `Showing ${data.length} stocks`;
    updatePlaceOrderButtons();
}

// ================================================================
// STRATEGY DROPDOWN - Change strategy and update table
// ================================================================
async function onStrategyChange() {
    const strategyId = document.getElementById('strategySelect').value;
    const strategy = STRATEGIES[strategyId];
    if (!strategy) return;

    // Update strategy details panel
    document.getElementById('infoStrategy').textContent = strategy.icon + ' ' + strategy.name;
    document.getElementById('infoRule').textContent = strategy.entryRule;
    document.getElementById('infoRisk').textContent = strategy.risk;

    // Check if this is Advance ORB (needs API call)
    if (strategyId === 'advanceorb') {
        // Show loading state
        const tbody = document.getElementById('screenerBody');
        const thead = document.querySelector('#screenerHead tr');
        const columns = [...strategy.columns];
        columns.push('Action');
        thead.innerHTML = columns.map(col => `<th>${col}</th>`).join('');
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;">🔎 Filtering best-performing stocks…</td></tr>`;
        document.getElementById('screenerCount').textContent = 'Loading...';

        // Fetch from backend
        const result = await fetchAdvanceORB();
        if (result) {
            lastAdvanceOrbData = result;
            renderStrategyData(result);
            startAdvanceOrbAutoRefresh();
        } else {
            tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--color-danger);">❌ Failed to load data. Please try again.</td></tr>`;
            document.getElementById('screenerCount').textContent = '0 stocks';
        }
        return;
    }

    // For other strategies (SmartMoney, Big Players) - use hardcoded data
    // Update table headers
    const thead = document.querySelector('#screenerHead tr');
    const columns = [...strategy.columns];
    columns.push('Action');
    thead.innerHTML = columns.map(col => `<th>${col}</th>`).join('');

    // Update table rows
    const tbody = document.getElementById('screenerBody');
    if (strategy.data.length === 0) {
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--text-muted);">No stocks found for this strategy.</td></tr>`;
    } else {
        tbody.innerHTML = strategy.data.map(row => {
            let displayRow = { ...row };
            
            // For SmartMoney, combine columns for better display
            if (strategyId === 'smartmoney') {
                displayRow.priceChange = `${row.price}<br><span style="color:${row.change.includes('+') ? 'var(--color-success)' : 'var(--color-danger)'}">${row.change}</span>`;
                displayRow.volumeRelvol = `${row.volume}<br>${row.relvol}`;
                displayRow.pocGap = `${row.poc}<br>${row.gap}`;
            }

            // Build values based on column order
            const values = [];
            columns.forEach(col => {
                if (col === 'Action') return;
                
                let value = '';
                if (col === 'Symbol') value = displayRow.symbol || '';
                else if (col === 'Max Qty') value = displayRow.maxQty || '';
                else if (col === 'Price / Chg%') value = displayRow.priceChange || '';
                else if (col === 'Volume / Rel Vol') value = displayRow.volumeRelvol || '';
                else if (col === 'Signal Time') value = displayRow.signalTime || '';
                else if (col === 'POC / Gap') value = displayRow.pocGap || '';
                else if (col === 'Signal Price / % Chg') value = displayRow.signalPrice || '';
                else if (col === 'Prev High') value = displayRow.prevHigh || '';
                else if (col === 'Candle Status') value = displayRow.candleStatus || '';
                else if (col === 'Price') value = displayRow.price || '';
                else if (col === 'CHG%') value = displayRow.change || '';
                else if (col === 'GAP%') value = displayRow.gap || '';
                else if (col === 'Volume') value = displayRow.volume || '';
                else if (col === 'RELVOL') value = displayRow.relvol || '';
                else if (col === 'Inside') value = displayRow.inside || '';
                else if (col === 'Breakout') value = displayRow.breakout || '';
                else if (col === '200 EMA') value = displayRow.ema || '';
                else if (col === '9:15 HIGH') value = displayRow.high915 || '';
                else if (col === 'PREV HIGH') value = displayRow.prevHigh || '';
                else if (col === 'MaxQty') value = displayRow.maxQty || '';
                else if (col === 'Sector') value = displayRow.sector || '';
                else if (col === 'Support Price') value = displayRow.supportPrice || '';
                else value = displayRow[col.toLowerCase()] || '';
                
                values.push(value);
            });

            const symbol = row.symbol || 'Unknown';
            return `<tr>
                ${values.map(val => `<td>${val}</td>`).join('')}
                <td>
                    <button class="btn-place-order btn-sm" onclick="placeOrder('${symbol}')" ${autoBuyEnabled ? 'disabled' : ''}>
                        Place Order
                    </button>
                </td>
            </tr>`;
        }).join('');
    }

    document.getElementById('screenerCount').textContent = `Showing ${strategy.data.length} stocks`;
    updatePlaceOrderButtons();
}

// ================================================================
// AUTO BUY TOGGLE
// ================================================================
function toggleAutoBuyMode() {
    const toggle = document.getElementById('autoBuyToggle');
    const status = document.getElementById('toggleStatus');
    
    autoBuyEnabled = toggle.checked;
    
    if (autoBuyEnabled) {
        status.textContent = 'ON';
        status.classList.add('active');
        showToast('🤖 Auto Buy ON', 'Auto-buy enabled for current strategy');
        autoBuyAllStocks();
    } else {
        status.textContent = 'OFF';
        status.classList.remove('active');
        showToast('👤 Manual Mode ON', 'Click Place Order to buy stocks');
    }
    
    onStrategyChange();
}

// ================================================================
// UPDATE PLACE ORDER BUTTONS
// ================================================================
function updatePlaceOrderButtons() {
    document.querySelectorAll('.btn-place-order').forEach(btn => {
        btn.disabled = autoBuyEnabled;
        btn.title = autoBuyEnabled ? 'Auto Buy is ON - manual orders disabled' : 'Click to place order';
    });
}

// ================================================================
// AUTO BUY ALL STOCKS
// ================================================================
function autoBuyAllStocks() {
    const strategyId = document.getElementById('strategySelect').value;
    const strategy = STRATEGIES[strategyId];
    if (!strategy || strategy.data.length === 0) {
        showToast('⚠️ No Stocks', 'No stocks to auto-buy');
        return;
    }
    const symbols = strategy.data.map(row => row.symbol || 'Unknown');
    showToast('🚀 Auto-Buy All', `Buying ${symbols.length} stocks from ${strategy.name}: ${symbols.join(', ')}`);
}

// ================================================================
// LIGHTWEIGHT REFRESH (TradingView only, no Yahoo candle round-trip)
// ================================================================
let lastAdvanceOrbData = null;
let advanceOrbAutoTimer = null;
const AUTO_REFRESH_MS = 30000;

async function fetchAdvanceORBRefresh(silent = true) {
    if (!lastAdvanceOrbData || !lastAdvanceOrbData.data || lastAdvanceOrbData.data.length === 0) {
        return;
    }
    const symbols = lastAdvanceOrbData.data.map(r => r.Symbol).filter(Boolean);
    if (symbols.length === 0) return;
    try {
        const response = await fetch(
            `/api/strategies/advanceorb/refresh?tickers=${encodeURIComponent(symbols.join(','))}`,
            { cache: 'no-store' }
        );
        if (!response.ok) return;
        const result = await response.json();
        const bySymbol = {};
        for (const r of (result.refreshed || [])) {
            bySymbol[r.Symbol] = r;
        }
        let touched = 0;
        for (const row of lastAdvanceOrbData.data) {
            const updated = bySymbol[row.Symbol];
            if (!updated) continue;
            if (typeof updated.Price === 'number') row.Price = updated.Price;
            if (typeof updated['CHG%'] === 'number') row['CHG%'] = updated['CHG%'];
            if (typeof updated.Volume === 'string') row.Volume = updated.Volume;
            if (typeof updated.RELVOL === 'string') row.RELVOL = updated.RELVOL;
            touched++;
        }
        // Always update in-memory data so it stays fresh for when the user returns,
        // but only re-render the table when Advance ORB is actually on-screen so we
        // don't stomp over SmartMoney / Big Players views.
        if (touched > 0) {
            const activePage = document.querySelector('.page.active');
            const onScreener = activePage && activePage.id === 'page-screener';
            const strategyId = document.getElementById('strategySelect')?.value;
            const isAdvanceOrb = strategyId === 'advanceorb';
            if (onScreener && isAdvanceOrb) {
                renderStrategyData(lastAdvanceOrbData);
                if (!silent) showToast('🔄 Refreshed', `${touched} stocks updated`);
            }
        }
    } catch (e) {
        console.error('Refresh failed:', e);
    }
}

function startAdvanceOrbAutoRefresh() {
    stopAdvanceOrbAutoRefresh();
    // Always tick — the user wants the advanceorb dataset to stay current even when
    // they're on Home / Portfolio / Strategies / SmartMoney / Big Players pages,
    // so the upcoming auto-buy logic sees fresh prices. fetchAdvanceORBRefresh will
    // silently update in-memory data and re-render only when Advance ORB is visible.
    advanceOrbAutoTimer = setInterval(() => {
        fetchAdvanceORBRefresh(true);
    }, AUTO_REFRESH_MS);
}

function stopAdvanceOrbAutoRefresh() {
    if (advanceOrbAutoTimer) {
        clearInterval(advanceOrbAutoTimer);
        advanceOrbAutoTimer = null;
    }
}

// Expose so main.js can stop the timer when leaving the Screener page.
window.startAdvanceOrbAutoRefresh = startAdvanceOrbAutoRefresh;
window.stopAdvanceOrbAutoRefresh = stopAdvanceOrbAutoRefresh;


// ================================================================
// REFRESH BUTTON (formerly "Run Screener")
// ================================================================
function refreshScreener() {
    const strategyId = document.getElementById('strategySelect')?.value;
    if (strategyId === 'advanceorb') {
        fetchAdvanceORBRefresh(false);
    } else {
        // SmartMoney / Big Players use hardcoded data; just re-render.
        onStrategyChange();
    }
}

// ================================================================
// PLACE ORDER
// ================================================================
function placeOrder(symbol) {
    if (autoBuyEnabled) {
        showToast('⚠️ Auto Buy ON', 'Disable Auto Buy to place manual orders');
        return;
    }
    showToast('📝 Order Placed', `Order placed for ${symbol}`);
}
