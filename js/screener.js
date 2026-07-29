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

// =====================================================================
// AUTO BUY PRICE-BAND FILTER (9:15 high breakout)
// =====================================================================
// Auto-buy only fires when the row's CURRENT PRICE has just broken
// ABOVE its 9:15 IST opening-candle high by a tight percentage band:
//     move_pct = (price - high915) / high915 * 100
// and the move sits inside this band:
//     [AUTO_BUY_MIN_MOVE_ABOVE_915_PCT,
//      AUTO_BUY_MAX_MOVE_ABOVE_915_PCT]
//
// Why these two thresholds:
//   • MIN_MOVE_ABOVE_915_PCT (0.15%) — smallest gap above the 9:15
//     high that still counts as a "real breakout". Tighter (smaller
//     number) = earlier entries but more false breakouts. The sweet
//     spot is 0.10–0.20%.
//   • MAX_MOVE_ABOVE_915_PCT (0.50%) — largest gap above the 9:15
//     high we'll accept. Beyond this the breakout is already
//     stretched → late-entry pullback risk.
//
// How to retune (no other file/code changes needed):
//   • Disable lower bound (still allow entry even if price is AT or
//     BELOW 9:15 high): set MIN_MOVE_ABOVE_915_PCT = 0 (or any negative).
//   • Disable upper cap (no stretch limit; ride the runaway):
//     set MAX_MOVE_ABOVE_915_PCT = 100 (or any large number).
//   • Tighter both bounds  (e.g. 0.10 / 0.30) → fewer but more
//     confirmed breakouts.
//   • Looser both bounds   (e.g. 0.20 / 1.00) → more candidates
//     but later entries / FOMO trades.
// =====================================================================
const AUTO_BUY_MIN_MOVE_ABOVE_915_PCT = 0.15;
const AUTO_BUY_MAX_MOVE_ABOVE_915_PCT = 0.50;
// ===================================================================
// AUTO BUY PRICE-vs-EMA GATE (200-period EMA)
// ===================================================================
// User policy: an auto-buy order is only placed when the row's
// current price sits ABOVE the row's 200-period EMA. The EMA is
// computed server-side per row from yfinance 5-min candles (see
// compute_200_ema_batch in advance_orb/app.py). If the EMA fetch
// failed for a row (rate-limited, 401, delisted, etc.) we treat
// it as "skip" — never submit on a candle we couldn't validate.
// ===================================================================
const AUTO_BUY_REQUIRE_PRICE_ABOVE_EMA = true;

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
    // 300ms debounce — collapse rapid +/- clicks into a single
    // refetch. We never trigger a full screener refresh (TV scan
    // + 200 EMA pull + 9:15 candle pull) on a budget/parts stepper
    // change; that's expensive and not needed. MaxQty is a pure
    // formula on price + budget + parts + margin-per-share — so
    // we recompute it locally over the existing snapshot.
    clearTimeout(_stepperRefreshTimer);
    _stepperRefreshTimer = setTimeout(async () => {
        const strategyId = document.getElementById("strategySelect")?.value;
        if (strategyId !== "advanceorb") return;

        // No snapshot yet — fall back to a heavy full fetch (the
        // Refresh button and Strategy dropdown paths already do
        // this; calling onStrategyChange() covers both cases).
        if (!lastAdvanceOrbData || !Array.isArray(lastAdvanceOrbData.data) || lastAdvanceOrbData.data.length === 0) {
            await onStrategyChange();
            return;
        }

        // Snapshot exists: hit the lightweight qty-only endpoint
        // POST /api/strategies/advanceorb/qty with {Symbol,Price}
        // pairs and merge MaxQty back into the row buffer in place.
        const budget = _readBudget();
        const parts  = _readParts();
        const symbols = lastAdvanceOrbData.data
            .map(r => ({ Symbol: r.Symbol, Price: r.Price }))
            .filter(p => p.Symbol && Number.isFinite(parseFloat(p.Price)));

        try {
            const resp = await fetch('/api/strategies/advanceorb/qty', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ budget, parts, symbols }),
            });
            if (!resp.ok) throw new Error(`qty-refetch HTTP ${resp.status}`);
            const json = await resp.json();
            const qtyMap = new Map();
            for (const e of (json.data || [])) qtyMap.set(e.Symbol, e.MaxQty);
            let touched = 0;
            for (const row of lastAdvanceOrbData.data) {
                if (qtyMap.has(row.Symbol)) {
                    row.MaxQty = qtyMap.get(row.Symbol);
                    touched++;
                }
            }
            if (typeof renderStrategyData === 'function') {
                renderStrategyData(lastAdvanceOrbData);
            }
            console.info(`[qty-stepper] refreshed MaxQty on ${touched}/${lastAdvanceOrbData.data.length} rows (budget=${budget} parts=${parts})`);
        } catch (e) {
            console.error('[qty-stepper] lightweight recompute failed:', e);
            showToast('⚠️ Stale MaxQty', 'Failed to refresh MaxQty — hit Refresh to retry.');
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

                // ---- column-name -> row-key fallbacks ----
                // Server columns ("200 EMA", "9:15 HIGH") use spaces
                // but row keys are snake-style ("ema", "high915").
                // Map those by hand before falling back to the generic
                // row[col] | row[col-no-spaces] | row[col-lower] chain
                // (which would otherwise yield empty cells because
                // the row payload uses different keys than the
                // headers).
                let value;
                if (col === '200 EMA') {
                    const ema = parseFloat(row.ema);
                    value = Number.isFinite(ema) ? ema : '';
                } else if (col === '1st High') {
                    const high = parseFloat(row.high915);
                    value = Number.isFinite(high) ? high.toFixed(2) : '';
                } else if (col === '1st Low') {
                    const low = parseFloat(row.low915);
                    value = Number.isFinite(low) ? low.toFixed(2) : '';
                } else if (col === '1st Range%') {
                    const range = parseFloat(row.candle_range_pct);
                    value = Number.isFinite(range) ? `${range.toFixed(2)}%` : '';
                } else if (col === '9:15 HIGH') {
                    const h = parseFloat(row.high915);
                    value = Number.isFinite(h) ? h : '';
                } else {
                    const colKey = col.replace(/ /g, '').replace(/\//g, '');
                    value = row[col] || row[colKey] || row[col.toLowerCase()] || '';
                }
                
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

    // ============================================================
    // CASE 1: ADVANCE ORB (API call)
    // ============================================================
    if (strategyId === 'advanceorb') {
        const tbody = document.getElementById('screenerBody');
        const thead = document.querySelector('#screenerHead tr');
        const columns = [...strategy.columns];
        columns.push('Action');
        thead.innerHTML = columns.map(col => `<th>${col}</th>`).join('');
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;">🔎 Filtering best-performing stocks…</td></tr>`;
        document.getElementById('screenerCount').textContent = 'Loading...';

        const result = await fetchAdvanceORB();
        if (result) {
            lastAdvanceOrbData = result;
            renderStrategyData(result);
            startAdvanceOrbAutoRefresh();
            // Stop Big Players refresh if running
            if (typeof stopBigPlayersAutoRefresh === 'function') stopBigPlayersAutoRefresh();
        } else {
            tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--color-danger);">❌ Failed to load data. Please try again.</td></tr>`;
            document.getElementById('screenerCount').textContent = '0 stocks';
        }
        return;
    }

    // ============================================================
    // CASE 2: BIG PLAYERS (API call)
    // ============================================================
    if (strategyId === 'bigplayers') {
        const tbody = document.getElementById('screenerBody');
        const thead = document.querySelector('#screenerHead tr');
        const columns = [...strategy.columns];
        columns.push('Action');
        thead.innerHTML = columns.map(col => `<th>${col}</th>`).join('');
        tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;">🏢 Fetching Big Players data…</td></tr>`;
        document.getElementById('screenerCount').textContent = 'Loading...';

        // Call Big Players API
        const result = await window.fetchBigPlayers();
        if (result) {
            window.lastBigPlayersData = result;
            window.renderBigPlayersData(result);
            window.startBigPlayersAutoRefresh();
            // Stop Advance ORB refresh if running
            if (typeof stopAdvanceOrbAutoRefresh === 'function') stopAdvanceOrbAutoRefresh();
        } else {
            tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--color-danger);">❌ Failed to load Big Players data</td></tr>`;
            document.getElementById('screenerCount').textContent = '0 stocks';
        }
        return;
    }

    // ============================================================
    // CASE 3: SMARTMONEY (Hardcoded data)
    // ============================================================
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
        // Toggling Auto-Buy ON/OFF must NOT re-fetch the screener.
        // The table already has the candidate set in `lastAdvanceOrbData`
        // from the last Refresh / auto-refresh / strategy load. If the
        // screener hasn't been loaded yet, we surface a "Run Screener
        // First" toast and bail. (Previously this function called
        // onStrategyChange() which wiped the table to the loading
        // placeholder and re-fired fetchAdvanceORB() — wrong.)
        if (lastAdvanceOrbData && Array.isArray(lastAdvanceOrbData.data) && lastAdvanceOrbData.data.length > 0) {
            autoBuyAllStocks();
        } else {
            showToast('⚠️ Run Screener First', 'Click Refresh to load stocks before auto-buy.');
        }
    } else {
        status.textContent = 'OFF';
        status.classList.remove('active');
        showToast('👤 Manual Mode ON', 'Click Place Order to buy stocks');
    }
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
// AMO MODE  (global switch — Market vs After-Market Order)
// ================================================================
// Toggled from index.html via onchange="toggleAmoMode()". Default
// false = regular market order. When true, every subsequent
// placeOrder() / autoBuyAllStocks() submission sets the dhan
// afterMarketOrder=true flag with amoTime="OPEN" (queued for 9:15).
let amoEnabled = false;

function toggleAmoMode() {
    amoEnabled = !!document.getElementById('amoToggle')?.checked;
    const status = document.getElementById('amoStatus');
    if (!status) return;
    status.textContent = amoEnabled ? 'AMO' : 'MKT';
    status.classList.toggle('active', amoEnabled);
    showToast(
        amoEnabled ? '🌙 AMO Mode ON' : '⚡ Market Mode ON',
        amoEnabled ? 'Orders will queue for market open (9:15 IST)'
                   : 'Orders will be live market orders'
    );
}

// ================================================================
// ORDER HELPERS
// ================================================================
function _orderRowForSymbol(symbol) {
    if (!lastAdvanceOrbData || !Array.isArray(lastAdvanceOrbData.data)) return null;
    const target = String(symbol || '').trim().toUpperCase();
    return lastAdvanceOrbData.data.find(
        r => r && r.Symbol && String(r.Symbol).toUpperCase() === target
    ) || null;
}

function _openConfirmModal(order) {
    const overlay = document.getElementById('confirmModalOverlay');
    if (!overlay) return;
    document.getElementById('confirmModalTitle').textContent =
        order.afterMarketOrder ? '🌙 Confirm AMO' : '⚡ Confirm Buy';
    document.getElementById('confirmModalLine1').textContent = order.symbol;
    const priceText = (typeof order.price === 'number' && order.price)
        ? `₹${order.price.toFixed(2)}` : 'market price';
    const totalText = (typeof order.total === 'number' && order.total > 0)
        ? `≈ ₹${order.total.toFixed(0)}` : '≈ margin-dependent';
    document.getElementById('confirmModalLine2').textContent =
        `${order.qty} shares × ${priceText} ${totalText}`;
    document.getElementById('confirmModalLine3').textContent =
        `${order.productType}${order.afterMarketOrder ? ' · AMO (queued for 9:15)' : ' · Market'}`;
    document.getElementById('confirmModalConfirmBtn').onclick = () => _submitOrder(order);
    overlay.classList.add('show');
}

function _closeConfirmModal() {
    const overlay = document.getElementById('confirmModalOverlay');
    if (overlay) overlay.classList.remove('show');
}

async function _submitOrder(order, source = 'manual') {
    const overlay = document.getElementById('confirmModalOverlay');
    const body = {
        symbol: order.symbol,
        quantity: order.qty,
        transactionType: 'BUY',
        productType: order.productType || 'INTRADAY',
        afterMarketOrder: !!order.afterMarketOrder,
        amoTime: 'OPEN',
    };
    try {
        const r = await fetch('/api/orders/place', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const result = await r.json().catch(() => ({}));
        if (overlay) overlay.classList.remove('show');
        if (r.ok && result.success) {
            showToast('✅ Order Placed',
                `${order.symbol} → ${result.order_id}${body.afterMarketOrder ? ' (AMO)' : ''}`);
        } else {
            const msg = result?.error || `HTTP ${r.status}`;
            showToast(`❌ ${order.symbol} failed`, msg);
            console.error('place_order result:', result);
        }
    } catch (e) {
        if (overlay) overlay.classList.remove('show');
        showToast('❌ Network', e.message || 'request failed');
    }
}

// ================================================================
// AUTO BUY ALL STOCKS  (top-5 by screener order, parallel submit)
// ================================================================
async function autoBuyAllStocks() {
    if (!lastAdvanceOrbData || !Array.isArray(lastAdvanceOrbData.data) || lastAdvanceOrbData.data.length === 0) {
        showToast('⚠️ No Stocks', 'Screener has no rows to auto-buy');
        return;
    }
    // User policy: at most AUTO_BUY_CAP stocks/day.
    // We do NOT pre-slice the table to the first 5 by CHG% — a setup
    // that matches the 9:15 high breakout band might not appear in the
    // first 5 movers. Instead, scan the entire screener table, let the
    // band filter pick what matches, then sort those by CHG% desc and
    // cap at AUTO_BUY_CAP.
    const AUTO_BUY_CAP = 5;
    const eligible = lastAdvanceOrbData.data
        .filter(r => r && r.Symbol && Number(r.MaxQty) > 0);

    if (eligible.length === 0) {
        showToast('⚠️ No Margin', 'No rows have MaxQty > 0 — adjust budget/parts');
        return;
    }

    // -----------------------------------------------------------------
    // 9:15 HIGH PRICE-BAND FILTER
    // -----------------------------------------------------------------
    // For every candidate in eligible, compute the gap of `price` above
    // its 9:15 IST opening-candle high. Keep only those whose
    //   move_pct = (price - high915) / high915 * 100
    // sits inside
    //   [AUTO_BUY_MIN_MOVE_ABOVE_915_PCT,
    //    AUTO_BUY_MAX_MOVE_ABOVE_915_PCT]
    // Below MIN : not a real breakout yet (price still at/under 9:15
    //            high). Skip — do NOT place an order.
    // Above MAX : breakout already stretched, late-entry/pullback
    //            risk. Skip — do NOT place an order.
    // Constants at the top of this file: AUTO_BUY_MIN/MAX_MOVE...
    // -----------------------------------------------------------------
    const bandFiltered = eligible.filter(row => {
        const high915 = parseFloat(row.high915);
        const price = parseFloat(row.price);
        // Skip rows missing either anchor (zero / NaN) — can't decide.
        if (!Number.isFinite(high915) || high915 <= 0) return false;
        if (!Number.isFinite(price) || price <= 0) return false;
        const movePct = ((price - high915) / high915) * 100;
        // The actual condition the user asked for:
        //   if price < +0.15% above 9:15 high  → DO NOT execute
        //   if price > +0.50% above 9:15 high  → DO NOT execute
        return movePct >= AUTO_BUY_MIN_MOVE_ABOVE_915_PCT
            && movePct <= AUTO_BUY_MAX_MOVE_ABOVE_915_PCT;
    });

    if (bandFiltered.length === 0) {
        // 0/N rows matched. Surface the actual move_pct per row so the
        // user can see whether the band is too tight, too loose, or
        // whether price is actually below 9:15 high entirely.
        const expectedBand = `+${AUTO_BUY_MIN_MOVE_ABOVE_915_PCT}–+${AUTO_BUY_MAX_MOVE_ABOVE_915_PCT}%`;
        const probes = eligible.map(row => {
            const high915 = parseFloat(row.high915);
            const price = parseFloat(row.price);
            if (!Number.isFinite(high915) || high915 <= 0 ||
                !Number.isFinite(price) || price <= 0) {
                return `${row.Symbol}: missing price / 9:15 high`;
            }
            const m = ((price - high915) / high915) * 100;
            return `${row.Symbol}: ${m >= 0 ? '+' : ''}${m.toFixed(2)}%`;
        });
        const previewProbes = probes.slice(0, 3).join(' · ') + (probes.length > 3 ? '…' : '');
        showToast(
            '⚠️ No Setup',
            `0/${eligible.length} in 9:15 ${expectedBand} band — ${previewProbes}`
        );
        console.warn('[auto-buy] band filter rejected all', eligible.length, 'rows:', {
            MIN: AUTO_BUY_MIN_MOVE_ABOVE_915_PCT,
            MAX: AUTO_BUY_MAX_MOVE_ABOVE_915_PCT,
            probes,
        });
        return;
    }

    // Sort band-matching rows by CHG% descending. Use the numeric
    // `change_pct` column added by the 200-EMA filter on the backend
    // (falls back to parsing the formatted "change" string for safety).
    // ---------------------------------------------------------------
    // 200-EMA PRICE GATE (above-the-trend filter)
    // ---------------------------------------------------------------
    // User policy: only place an auto-buy order when the row's
    // CURRENT price is ABOVE the 200-period EMA. This filters
    // out falling-knife rows where yfinance still produced an EMA
    // from recent candles but the price has been below the trend
    // all session. A missing EMA (server couldn't fetch) is treated
    // as "skip"; we never commit on unvalidated data.
    // ---------------------------------------------------------------
    const aboveEma = bandFiltered.filter(row => {
        if (!AUTO_BUY_REQUIRE_PRICE_ABOVE_EMA) return true;
        const ema = parseFloat(row.ema);
        const price = parseFloat(row.price);
        if (!Number.isFinite(ema)) return false;          // yfinance missed
        if (!Number.isFinite(price) || price <= 0) return false;
        return price > ema;
    });

    if (aboveEma.length === 0) {
        const expectedBand = `+${AUTO_BUY_MIN_MOVE_ABOVE_915_PCT}–+${AUTO_BUY_MAX_MOVE_ABOVE_915_PCT}%`;
        const emaProbes = bandFiltered.slice(0, 6).map(row => {
            const ema   = parseFloat(row.ema);
            const price = parseFloat(row.price);
            const hi    = parseFloat(row.high915);
            const band  = Number.isFinite(hi) && hi > 0 ? ((price - hi) / hi * 100) : null;
            const above = Number.isFinite(ema) ? price > ema : null;
            const bandStr = band != null ? `${band >= 0 ? '+' : ''}${band.toFixed(2)}%` : 'n/a';
            const emaStr  = Number.isFinite(ema) ? ema.toFixed(2) : 'n/a';
            const dir     = above === true ? '↑above' : above === false ? '↓below' : '—n/a';
            return `${row.Symbol || '?'}: p=${price} ema=${emaStr} band=${bandStr} ${dir}`;
        });
        showToast(
            '⚠️ No Band\u2003+\u2003EMA Match',
            `0/${eligible.length} in 9:15 ${expectedBand} AND price > 200-EMA — ${emaProbes.join(' | ')}`
        );
        console.warn('[auto-buy] EMA-gate rejected all', bandFiltered.length, 'band-passed rows. PREVIEW:', {
            AUTO_BUY_REQUIRE_PRICE_ABOVE_EMA,
            probes: emaProbes,
        });
        return;
    }

    // Sort survivors by CHG% desc — strongest momentum first.
    aboveEma.sort((a, b) => {
        const ap = (a.change_pct != null && Number.isFinite(parseFloat(a.change_pct)))
            ? parseFloat(a.change_pct) : _parsePctStr(a.change);
        const bp = (b.change_pct != null && Number.isFinite(parseFloat(b.change_pct)))
            ? parseFloat(b.change_pct) : _parsePctStr(b.change);
        return bp - ap;
    });
    const topN = aboveEma.slice(0, AUTO_BUY_CAP);

    if (topN.length < eligible.length) {
        console.warn(
            `[auto-buy] band+EMA filter + cap selected ${topN.length}/${eligible.length} eligible rows:`,
            topN.map(r => `${r.Symbol} (chg=${r['CHG%']} p=${r.price} ema=${r.ema ?? 'n/a'})`)
        );
    }

    const orders = topN.map(r => ({
        symbol: r.Symbol,
        quantity: parseInt(r.MaxQty, 10) || 0,
        transactionType: 'BUY',
        productType: 'INTRADAY',
        afterMarketOrder: amoEnabled,
        amoTime: 'OPEN',
    }));

    showToast(
        '🚀 Auto-Buy Started',
        `Submitting ${orders.length} stock(s)${amoEnabled ? ' as AMO' : ''} in parallel — 9:15 +${AUTO_BUY_MIN_MOVE_ABOVE_915_PCT}–+${AUTO_BUY_MAX_MOVE_ABOVE_915_PCT}% band AND price > 200-EMA`
    );

    try {
        const r = await fetch('/api/orders/place-batch', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                orders,
                productType: 'INTRADAY',
                afterMarketOrder: amoEnabled,
                amoTime: 'OPEN',
                source: 'auto_buy',
            }),
        });
        const result = await r.json().catch(() => ({}));
        if (!r.ok) {
            showToast('❌ Auto-Buy Failed', result.detail || `HTTP ${r.status}`);
            console.error('auto_buy batch result:', result);
            return;
        }
        const s = result.succeeded || 0;
        const t = result.total || orders.length;
        if (s === t) {
            showToast('✅ Auto-Buy Complete', `${s}/${t} orders placed`);
        } else if (s > 0) {
            const failed = (result.results || [])
                .filter(x => x && !x.success)
                .map(x => `${x.symbol}: ${x.error}`)
                .slice(0, 3)
                .join('  ·  ');
            showToast('⚠️ Partial', `${s}/${t} succeeded — ${failed || 'see console'}`);
            console.warn('Auto-buy partial result:', result);
        } else {
            showToast('❌ Auto-Buy Failed', `0/${t} succeeded — see console`);
            console.error('Auto-buy batch result:', result);
        }
    } catch (e) {
        showToast('❌ Network', e.message || 'request failed');
    }
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
        // The refresh endpoint re-checks the first 5-minute candle. If a
        // candle has moved above the 1.5% maximum since the full scan, its
        // symbol is intentionally absent from `refreshed`; remove it from
        // the local dataset before rendering or auto-buy evaluates it.
        const validSymbols = new Set(Object.keys(bySymbol));
        lastAdvanceOrbData.data = lastAdvanceOrbData.data.filter(
            row => validSymbols.has(row.Symbol)
        );
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
// P3 — LIVE TICK STREAM (Angel One WebSocket overlay via SSE)
// ================================================================
// Opens a single EventSource connection to
// ``/api/market/live-ticks/stream`` and patches Price / CHG% /
// Volume cells in-place on every pushed tick (~250 ms interval).
// No polling — the server pushes changes as they happen.
let _tickEventSource = null;
let _lastTickPayload = null;

function _applyTicks(ticks) {
    const rows = document.querySelectorAll('#screenerBody tr');
    if (!rows.length) return;
    const headers = Array.from(document.querySelectorAll('#screenerHead th'));
    const priceIdx = headers.findIndex(h => h.textContent.trim() === 'Price');
    const chgIdx   = headers.findIndex(h => h.textContent.trim() === 'CHG%');
    const volIdx   = headers.findIndex(h => h.textContent.trim() === 'Volume');
    if (priceIdx < 0) return;

    for (const tr of rows) {
        const cells = tr.querySelectorAll('td');
        const symEl = cells[0];
        if (!symEl) continue;
        const sym = symEl.textContent.trim();
        const tick = ticks[sym];
        if (!tick) continue;

        if (tick.ltp != null && priceIdx < cells.length) {
            cells[priceIdx].textContent = `₹${Number(tick.ltp).toFixed(2)}`;
            // Flash cell briefly to show update
            cells[priceIdx].style.transition = 'background 0.15s';
            cells[priceIdx].style.background = 'rgba(34,197,94,0.15)';
            setTimeout(() => { cells[priceIdx].style.background = ''; }, 300);
        }
        if (tick.change_pct != null && chgIdx >= 0 && chgIdx < cells.length) {
            const v = Number(tick.change_pct);
            const sign = v > 0 ? '+' : '';
            cells[chgIdx].textContent = `${sign}${v.toFixed(2)}%`;
            cells[chgIdx].style.color = v >= 0 ? 'var(--color-green, #22c55e)' : 'var(--color-red, #ef4444)';
        }
        if (tick.volume != null && volIdx >= 0 && volIdx < cells.length) {
            const vol = Number(tick.volume);
            let txt;
            if (vol >= 1_000_000) txt = `${(vol / 1_000_000).toFixed(1)}M`;
            else if (vol >= 1_000) txt = `${(vol / 1_000).toFixed(1)}K`;
            else txt = String(vol);
            cells[volIdx].textContent = txt;
        }

        if (lastAdvanceOrbData && lastAdvanceOrbData.data) {
            const row = lastAdvanceOrbData.data.find(r => r.Symbol === sym);
            if (row) {
                if (tick.ltp != null) row.Price = Number(tick.ltp);
                if (tick.change_pct != null) row['CHG%'] = Number(tick.change_pct);
            }
        }
    }
}

function startLiveTickPoll() {
    stopLiveTickPoll();
    const url = '/api/market/live-ticks/stream';
    _tickEventSource = new EventSource(url);
    _tickEventSource.onmessage = function (ev) {
        try {
            const data = JSON.parse(ev.data);
            if (data.connected && data.ticks) {
                _lastTickPayload = data.ticks;
                const activePage = document.querySelector('.page.active');
                const onScreener = activePage && activePage.id === 'page-screener';
                const strategyId = document.getElementById('strategySelect')?.value;
                if (onScreener && (strategyId === 'advanceorb' || strategyId === 'bigplayers')) {
                    _applyTicks(data.ticks);
                }
            }
        } catch (_) {}
    };
    _tickEventSource.onerror = function () {
        // EventSource auto-reconnects; nothing more to do
    };
}

function stopLiveTickPoll() {
    if (_tickEventSource) {
        _tickEventSource.close();
        _tickEventSource = null;
    }
}

// Hook into existing auto-refresh start/stop so live ticks
// run alongside the 30 s candle check.
const _origStart = startAdvanceOrbAutoRefresh;
startAdvanceOrbAutoRefresh = function () {
    _origStart.call(this);
    startLiveTickPoll();
};
const _origStop = stopAdvanceOrbAutoRefresh;
stopAdvanceOrbAutoRefresh = function () {
    _origStop.call(this);
    stopLiveTickPoll();
};
window.startLiveTickPoll = startLiveTickPoll;
window.stopLiveTickPoll  = stopLiveTickPoll;


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

function placeOrder(symbol) {
    if (autoBuyEnabled) {
        showToast('⚠️ Auto Buy ON', 'Disable Auto Buy to place manual orders');
        return;
    }
    const qty = _lookupRowQty(symbol);
    if (qty < 1) {
        showToast('⚠️ No Margin', `${symbol}: MaxQty = 0 — Place Order is disabled`);
        return;
    }
    const price = _lookupRowPrice(symbol);
    _openConfirmModal({
        symbol,
        qty,
        price,
        total: qty * price,
        productType: 'INTRADAY',
        afterMarketOrder: amoEnabled,
        source: 'manual',
    });
}

// Parse "+12.34%" / "-1.20%" / "0.45%" → 12.34 / -1.20 / 0.45.
// Used by auto-buy's CHG%-desc sort fallback when `change_pct`
// (numeric) is not present in the row.
function _parsePctStr(s) {
    if (s == null) return 0;
    const m = String(s).match(/-?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : 0;
}

// Locate the row's Price cell (DOM lookup — works for both live data
// and the SmartMoney / BigPlayers hardcoded stub rows).
function _lookupRowPrice(symbol) {
    try {
        const headers = document.querySelectorAll('#screenerHead th');
        const tr = Array.from(document.querySelectorAll('#screenerBody tr')).find(r => {
            const first = r.querySelector('td');
            return first && first.textContent.trim() === symbol;
        });
        if (!tr) return 0;
        const cells = tr.querySelectorAll('td');
        for (let i = 0; i < headers.length && i < cells.length; i++) {
            if (headers[i].textContent.trim() === 'Price') {
                const v = parseFloat((cells[i].textContent || '').replace(/[^0-9.]/g, ''));
                return Number.isFinite(v) ? v : 0;
            }
        }
    } catch (e) { console.warn('price lookup failed for', symbol, e); }
    return 0;
}

