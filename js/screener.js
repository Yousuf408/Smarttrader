// ================================================================
// SCREENER PAGE
// ================================================================

// ================================================================
// BUDGET / PARTS STEPPERS — wired to MaxQty column
// ================================================================
// Persisted user-configurable trading capital + number of equal
// parts to split it into. Mirrors the calculator inputs:
//   qty = floor((budget / parts) / margin_per_share)
// Changes trigger a 300 ms debounced refetch /api/strategies/
// advanceorb?budget=X&parts=Y so MaxQty column updates live.
const BUDGET_KEY = "tradeAlgo.budget";
const PARTS_KEY  = "tradeAlgo.parts";
const BUDGET_DEFAULT = 100000;
const PARTS_DEFAULT  = 4;
let _stepperRefreshTimer = null;

function _getBudgetInput() { return document.getElementById("budgetInput"); }
function _getPartsInput()  { return document.getElementById("partsInput"); }

// Indian grouping: 100000 -> "1,00,000",  50000 -> "50,000",
// 5500 -> "5,500". Used as both display format and the canonical
// string representation of Budget in the input.
function _formatIndianNumber(n) {
    return Number(n || 0).toLocaleString('en-IN');
}

// Parsed integer ignoring commas / ₹ / spaces — source of truth for
// every code path that needs to fetch with a numeric budget.
function _readRawBudget() {
    const el = _getBudgetInput();
    if (!el) return BUDGET_DEFAULT;
    const raw = (el.dataset.value || el.value || '').replace(/[^\d]/g, '');
    const v = parseInt(raw, 10);
    return (Number.isFinite(v) && v > 0) ? v : BUDGET_DEFAULT;
}
// Backwards-compat alias — fetchAdvanceORB() calls _readBudget().
function _readBudget() { return _readRawBudget(); }
function _readParts() {
    const el = _getPartsInput();
    if (!el) return PARTS_DEFAULT;
    const v = parseInt(el.value, 10);
    return (Number.isFinite(v) && v >= 1 && v <= 20) ? v : PARTS_DEFAULT;
}

function _persistBudget(v) { try { localStorage.setItem(BUDGET_KEY, String(v)); } catch (e) {} }
function _persistParts(v)  { try { localStorage.setItem(PARTS_KEY,  String(v)); } catch (e) {} }

function _restoreSteppers() {
    let budget = BUDGET_DEFAULT;
    let parts  = PARTS_DEFAULT;
    try {
        const b = localStorage.getItem(BUDGET_KEY);
        if (b && /^\d+$/.test(b)) budget = Math.max(5000, parseInt(b, 10));
        const p = localStorage.getItem(PARTS_KEY);
        if (p && /^\d+$/.test(p)) parts = Math.min(20, Math.max(1, parseInt(p, 10)));
    } catch (e) {}
    const bEl = _getBudgetInput();
    const pEl = _getPartsInput();
    if (bEl) {
        bEl.dataset.value = String(budget);
        bEl.value         = _formatIndianNumber(budget);
    }
    if (pEl) pEl.value = parts;
}

// Idempotent — wires focus / input / blur listeners that keep the
// budget field formatted as Indian grouping whenever the user
// edits it. Called once at DOMContentLoaded.
function _attachBudgetFormatter() {
    const el = _getBudgetInput();
    if (!el || el.dataset.fmtAttached === '1') return;
    el.dataset.fmtAttached = '1';

    el.addEventListener('focus', () => {
        const raw = el.dataset.value || (el.value || '').replace(/[^\d]/g, '');
        el.dataset.value = raw;
        el.value = raw;
        setTimeout(() => { try { el.select(); } catch (_) {} }, 0);
    });
    el.addEventListener('input', () => {
        const raw = (el.value || '').replace(/[^\d]/g, '').slice(0, 9);
        el.dataset.value = raw;
        el.value = raw ? _formatIndianNumber(parseInt(raw, 10)) : '';
    });
    el.addEventListener('blur', () => {
        let raw = parseInt(el.dataset.value || '0', 10);
        if (!Number.isFinite(raw) || raw <= 0) raw = BUDGET_DEFAULT;
        raw = Math.max(5000, raw);
        el.dataset.value = String(raw);
        el.value         = _formatIndianNumber(raw);
        _persistBudget(raw);
        _scheduleScreenerRefresh();
    });
}

function stepBudget(delta) {
    const el = _getBudgetInput();
    if (!el) return;
    const next = Math.max(5000, _readBudget() + delta);
    el.value = next;
    _persistBudget(next);
    _scheduleScreenerRefresh();
}

function stepParts(delta) {
    const el = _getPartsInput();
    if (!el) return;
    const next = Math.min(20, Math.max(1, _readParts() + delta));
    el.value = next;
    _persistParts(next);
    _scheduleScreenerRefresh();
}

async function _scheduleScreenerRefresh() {
    clearTimeout(_stepperRefreshTimer);
    _stepperRefreshTimer = setTimeout(async () => {
        const strategyId = document.getElementById("strategySelect")?.value;
        if (strategyId !== "advanceorb") return;
        const result = await fetchAdvanceORB();
        if (result) {
            lastAdvanceOrbData = result;
            renderStrategyData(result);
            if (typeof startAdvanceOrbAutoRefresh === "function") {
                startAdvanceOrbAutoRefresh();
            }
        }
    }, 300);
}

document.addEventListener("DOMContentLoaded", () => {
    const bEl = _getBudgetInput();
    const pEl = _getPartsInput();
    if (bEl) {
        bEl.addEventListener("change", () => {
            const next = Math.max(5000, _readBudget());
            bEl.value = next;
            _persistBudget(next);
            _scheduleScreenerRefresh();
        });
    }
    if (pEl) {
        pEl.addEventListener("change", () => {
            const next = Math.min(20, Math.max(1, _readParts()));
            pEl.value = next;
            _persistParts(next);
            _scheduleScreenerRefresh();
        });
    }
    _attachBudgetFormatter();
    _restoreSteppers();
});

// ================================================================
// FETCH ADVANCE ORB FROM BACKEND API
// ================================================================
async function fetchAdvanceORB() {
    const budget = _readBudget();
    const parts  = _readParts();
    try {
        const response = await fetch(
            `/api/strategies/advanceorb?budget=${budget}&parts=${parts}`
        );
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
function _lookupRowQty(symbol) {
    try {
        const headers = document.querySelectorAll('#screenerHead th');
        const tr = Array.from(document.querySelectorAll('#screenerBody tr')).find(r => {
            const first = r.querySelector('td');
            return first && first.textContent.trim() === symbol;
        });
        if (!tr) return 0;
        const cells = tr.querySelectorAll('td');
        for (let i = 0; i < headers.length && i < cells.length; i++) {
            if (headers[i].textContent.trim() === 'MaxQty') {
                const v = parseInt((cells[i].textContent || '').replace(/[^0-9-]/g, ''), 10);
                return Number.isFinite(v) ? v : 0;
            }
        }
    } catch (e) { console.warn('qty lookup failed for', symbol, e); }
    return 0;
}

async function placeOrder(symbol) {
    if (autoBuyEnabled) {
        showToast('\u26a0\ufe0f Auto Buy ON', 'Disable Auto Buy to place manual orders');
        return;
    }
    const qty = _lookupRowQty(symbol);
    if (qty === 0) {
        showToast('\u26a0\ufe0f Insufficient margin', `${symbol}: MaxQty = 0. Place Order is disabled.`);
        return;
    }
    try {
        const response = await fetch('/api/orders/place', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                symbol: symbol,
                qty: qty,
                side: 'BUY',
                product_type: 'INTRADAY',
                validity: 'DAY',
                source: 'manual',
                exchange: 'NSE',
            }),
        });
        const result = await response.json().catch(() => ({}));
        if (result.status === 'rejected') {
            showToast('\u26a0\ufe0f Rejected', result.reason || `${symbol} rejected`);
        } else if (result.status === 'queued') {
            showToast('\ud83d\udce5 Order queued', result.message || `Queued ${symbol}`);
        } else if (result.status === 'would_call_broker') {
            showToast('\ud83d\ude80 Order ready', result.message || `Ready for ${symbol}`);
        } else if (!response.ok) {
            showToast('\u274c Failed', `Backend returned ${response.status}`);
        } else {
            showToast('\u2139\ufe0f Order', `Order processed for ${symbol}`);
        }
    } catch (err) {
        console.error('placeOrder failed:', err);
        showToast('\u274c Error', `placeOrder failed: ${err && err.message}`);
    }
}
