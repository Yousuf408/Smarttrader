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

        // MaxQty is opt-in (Calc MaxQty toggle). When OFF, the table shows
        // 0s — the qty endpoint would compute from the live table anyway,
        // so skip the broker margin round-trip entirely to keep steppers cheap.
        if (!calcQtyEnabled) return;

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

function initScreener() {
    const bEl = _getBudgetInput();
    const pEl = _getPartsInput();
    if (bEl && !bEl.dataset.listenerAttached) {
        bEl.dataset.listenerAttached = "1";
        bEl.addEventListener("change", () => {
            const next = Math.max(5000, _readBudget());
            bEl.value = next;
            _persistBudget(next);
            _scheduleScreenerRefresh();
        });
    }
    if (pEl && !pEl.dataset.listenerAttached) {
        pEl.dataset.listenerAttached = "1";
        pEl.addEventListener("change", () => {
            const next = Math.min(20, Math.max(1, _readParts()));
            pEl.value = next;
            _persistParts(next);
            _scheduleScreenerRefresh();
        });
    }
    _attachBudgetFormatter();
    _restoreSteppers();
    // Restore persisted candle timeframe into the TF select.
    try {
        const savedTf = parseInt(localStorage.getItem(TF_KEY), 10);
        if (savedTf === 15) orbTimeframe = 15;
    } catch (e) {}
    const tfSel = document.getElementById('tfSelect');
    if (tfSel) tfSel.value = String(orbTimeframe);
}

document.addEventListener("DOMContentLoaded", () => {
    initScreener();
});

// ================================================================
// FETCH ADVANCE ORB FROM BACKEND API
// ================================================================
let nearHighEnabled = false;
let aboveEmaEnabled = false;
let _inside915Only = false;
let _inside3Only = false;
let calcQtyEnabled = false;
// Candle timeframe (5 or 15 min) for Advance ORB. All ORB candle logic —
// opening range, inside/3-candle checks, and the 200 EMA — is computed
// backend-side from bars of this size.
const TF_KEY = "tradeAlgo.timeframe";
let orbTimeframe = 5;

function orbTimeframeLabel() {
    return orbTimeframe === 15 ? '15-min' : '5-min';
}

function onTimeframeChange() {
    const sel = document.getElementById('tfSelect');
    const tf = sel ? parseInt(sel.value, 10) : 5;
    orbTimeframe = (tf === 15) ? 15 : 5;
    try { localStorage.setItem(TF_KEY, String(orbTimeframe)); } catch (e) {}
    showToast(`⏱️ ${orbTimeframeLabel()} timeframe`,
        orbTimeframe === 15
            ? 'Opening range, inside/3-candle checks & 200 EMA now use 15-min bars (opening candle closes ~9:30)'
            : 'Using 5-min opening-range candles (closes ~9:20)');
    onStrategyChange();
}

function toggleNearHigh() {
    const toggle = document.getElementById('nearHighToggle');
    const status = document.getElementById('nearHighStatus');
    nearHighEnabled = toggle.checked;
    status.textContent = nearHighEnabled ? 'ON' : 'OFF';
    status.classList.toggle('active', nearHighEnabled);
    const hint = document.getElementById('nearHighHint');
    if (hint) hint.textContent = nearHighEnabled
        ? 'open within ±2% of prev high'
        : 'full TradingView universe';
    showToast(nearHighEnabled ? '🎯 Filter Near High ON' : '🎯 Filter Near High OFF',
        nearHighEnabled
            ? 'Keeping only stocks whose open is within ±2% of yesterday\'s high'
            : 'Showing full TradingView universe');
    // Re-fetch with the new mode
    onStrategyChange();
}

function toggleAboveEma() {
    const toggle = document.getElementById('aboveEmaToggle');
    const status = document.getElementById('aboveEmaStatus');
    aboveEmaEnabled = toggle.checked;
    status.textContent = aboveEmaEnabled ? 'ON' : 'OFF';
    status.classList.toggle('active', aboveEmaEnabled);
    const hint = document.getElementById('nearHighHint');
    if (hint) hint.textContent = aboveEmaEnabled
        ? 'close above 200 EMA (≤3% gap)'
        : 'open within ±2% of prev high';
    showToast(aboveEmaEnabled ? '📈 Above 200 EMA ON' : '📈 Above 200 EMA OFF',
        aboveEmaEnabled
            ? `Keeping only stocks whose opening-candle close is above the 200 ${orbTimeframeLabel()} EMA (max 3% above)`
            : 'No 200 EMA filter');
    // Re-fetch with the new mode
    onStrategyChange();
}

function toggleInside915() {
    const toggle = document.getElementById('inside915Toggle');
    const status = document.getElementById('inside915Status');
    _inside915Only = toggle.checked;
    status.textContent = _inside915Only ? 'ON' : 'OFF';
    status.classList.toggle('active', _inside915Only);
    showToast(_inside915Only ? '📐 Inside 9:15 ON' : '📐 Inside 9:15 OFF',
        _inside915Only
            ? `Showing only stocks where the 2nd ${orbTimeframeLabel()} candle closed inside the opening range`
            : 'Showing all stocks');
    // Re-fetch so the backend computes inside_915 from the Yahoo batch
    onStrategyChange();
}

function toggleInside3() {
    const toggle = document.getElementById('inside3Toggle');
    const status = document.getElementById('inside3Status');
    _inside3Only = toggle.checked;
    status.textContent = _inside3Only ? 'ON' : 'OFF';
    status.classList.toggle('active', _inside3Only);
    showToast(_inside3Only ? '📐 3 Closes Inside 9:15 ON' : '📐 3 Closes Inside 9:15 OFF',
        _inside3Only
            ? `Showing only stocks where 3 successive closes sit inside the opening range (${orbTimeframeLabel()})`
            : 'Showing all stocks');
    onStrategyChange();
}

// Opt-in MaxQty calculation. Off (default) = skip the broker margin
// round-trip on the screener load, so the table loads fast and the
// MaxQty column reads 0. On = compute MaxQty for each row via the
// connected broker (Dhan/Angel).
function toggleCalcQty() {
    const toggle = document.getElementById('calcQtyToggle');
    const status = document.getElementById('calcQtyStatus');
    calcQtyEnabled = toggle.checked;
    status.textContent = calcQtyEnabled ? 'ON' : 'OFF';
    status.classList.toggle('active', calcQtyEnabled);
    showToast(calcQtyEnabled ? '💸 Calc MaxQty ON' : '💸 Calc MaxQty OFF',
        calcQtyEnabled
            ? 'Computing MaxQty from connected broker margin'
            : 'Skipping MaxQty for faster load — broker margin not fetched');
    onStrategyChange();
}

async function fetchAdvanceORB() {
    const budget = _readBudget();
    const parts  = _readParts();
    const nh = nearHighEnabled ? '&near_high=true' : '&near_high=false';
    const ae = aboveEmaEnabled ? '&above_ema=true' : '&above_ema=false';
    const i9 = _inside915Only ? '&inside915=true' : '&inside915=false';
    const i3 = _inside3Only ? '&inside3=true' : '&inside3=false';
    const cq = calcQtyEnabled ? '&calc_qty=true' : '&calc_qty=false';
    const tf = `&timeframe=${orbTimeframe}`;
    try {
        const response = await fetch(
            `/api/strategies/advanceorb?budget=${budget}&parts=${parts}${nh}${ae}${i9}${i3}${cq}${tf}`
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
/** Copy symbol text to clipboard with 1-second highlight feedback. */
function copySymbol(text, el) {
    navigator.clipboard.writeText(text).catch(() => {});
    el.classList.add('symbol-cell-copied');
    setTimeout(() => el.classList.remove('symbol-cell-copied'), 1000);
}
// ================================================================
// SHARED ROW/CELL BUILDERS (used by both the main screener table and
// the "Breakout" table above it)
// ================================================================
/** Format one cell value for a column (mirrors the legacy inline logic). */
function orbCellValue(row, col) {
    let value;
    if (col === 'Signal' || col === 'Status' || col === 'Breakout') {
        const price = parseFloat(row.Price ?? row.price);
        const high = parseFloat(row.high915 ?? row['1st High'] ?? row.yesterday_high);
        const isBreakout = (Number.isFinite(price) && Number.isFinite(high) && high > 0 && price >= (high * 0.99)) || row.Breakout === 'Confirmed';
        const isNearHigh = (Number.isFinite(price) && Number.isFinite(high) && high > 0 && price >= (high * 0.98)) || row.near_high === true;
        
        if (isBreakout) {
            return `<span class="badge-signal badge-breakout">🚀 Breakout</span>`;
        } else if (isNearHigh) {
            return `<span class="badge-signal badge-nearhigh">⏳ Testing High</span>`;
        } else if (row.inside_915 === true || row['Inside 9:15'] === 'Yes') {
            return `<span class="badge-signal badge-inside">📐 Inside 9:15</span>`;
        } else {
            return `<span class="badge-signal badge-normal">⚪ In Range</span>`;
        }
    } else if (col === '200 EMA') {
        const ema = parseFloat(row.ema ?? row['200 EMA']);
        const price = parseFloat(row.Price ?? row.price);
        if (Number.isFinite(ema)) {
            const isAbove = Number.isFinite(price) && price >= ema;
            value = `<span style="color:${isAbove ? 'var(--color-success)' : 'inherit'};font-weight:${isAbove ? '600' : 'normal'}">${ema.toFixed(2)}</span>`;
        } else {
            value = '';
        }
    } else if (col === '1st High' || col === '9:15 HIGH') {
        const high = parseFloat(row.high915);
        value = Number.isFinite(high) ? high.toFixed(2) : '';
    } else if (col === '1st Low') {
        const low = parseFloat(row.low915);
        value = Number.isFinite(low) ? low.toFixed(2) : '';
    } else if (col === '1st Range%') {
        const range = parseFloat(row.candle_range_pct);
        value = Number.isFinite(range) ? `${range.toFixed(2)}%` : '';
    } else if (col === 'Inside 9:15' || col === 'Inside') {
        value = row.inside_915 ? '✅' : (row.inside_915 === false ? '❌' : '—');
    } else if (col === 'Share Low') {
        if (row.share_low && typeof row.share_low === 'object') {
            const m = row.share_low;
            value = m.current_time && m.matched_time
                ? `🎯 ${m.current_time} sharing low with ${m.matched_time} (${m.low} ≈ ${m.matched_low} · ${m.diff_pct}%)`
                : `🎯 ${m.low} (≈${m.matched_low} · ${m.diff_pct}%)`;
        } else {
            value = row.share_low ? '🎯' : '—';
        }
    } else if (col === 'Open 9:15') {
        const op = parseFloat(row.open915);
        value = Number.isFinite(op) ? `₹${op.toFixed(2)}` : '';
    } else if (col === 'Extra 15m Vol' || col === 'Extra 15m' || col === '15m Extra Vol' || col === '15m Vol' || col === '15M Vol') {
        const total15m = parseFloat(row.first_15m_vol ?? row['15m Vol']);
        const extraVol = parseFloat(row.extra_15m_vol ?? row['Extra 15m Vol']);
        const isHighest = row.is_15m_highest === true;
        
        const formatVol = (v) => {
            if (!Number.isFinite(v) || v <= 0) return '—';
            if (v >= 10000000) return `${(v / 10000000).toFixed(2)}Cr`;
            if (v >= 1000000) return `${(v / 1000000).toFixed(2)}M`;
            if (v >= 100000) return `${(v / 100000).toFixed(2)}L`;
            if (v >= 1000) return `${(v / 1000).toFixed(1)}k`;
            return `${Math.round(v)}`;
        };

        if (Number.isFinite(total15m) && total15m > 0) {
            const totalStr = formatVol(total15m);
            if (isHighest && Number.isFinite(extraVol) && extraVol > 0) {
                const extraStr = formatVol(extraVol);
                value = `<div style="display:flex;align-items:center;gap:6px;"><span style="font-weight:600;">${totalStr}</span><span class="badge-signal" style="background:rgba(34,197,94,0.15);color:var(--color-success);border:1px solid rgba(34,197,94,0.3);font-size:11px;padding:2px 6px;border-radius:4px;font-weight:700;" title="3-day highest! Extra volume: +${extraStr}">🔥 +${extraStr}</span></div>`;
            } else {
                value = `<span>${totalStr}</span>`;
            }
        } else if (isHighest && Number.isFinite(extraVol) && extraVol > 0) {
            value = `<span class="badge-signal" style="background:rgba(34,197,94,0.15);color:var(--color-success);border:1px solid rgba(34,197,94,0.3);font-weight:700;">🔥 +${formatVol(extraVol)}</span>`;
        } else {
            value = `<span style="color:var(--text-muted);">—</span>`;
        }

    } else if (col === 'Prev High' || col === 'PREV HIGH') {
        const ph = parseFloat(row.yesterday_high);
        value = Number.isFinite(ph) ? `₹${ph.toFixed(2)}` : '';
    } else {
        const colKey = col.replace(/ /g, '').replace(/\//g, '');
        value = row[col] || row[colKey] || row[col.toLowerCase()] || '';
    }
    if (col === 'Price' && typeof value === 'number') value = `₹${value.toFixed(2)}`;
    if (col === 'CHG%' && typeof value === 'number') {
        const sign = value > 0 ? '+' : '';
        const color = value >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
        value = `<span style="color:${color};font-weight:700;">${sign}${value.toFixed(2)}%</span>`;
    }
    if (col === 'GAP%' && typeof value === 'number') {
        const sign = value > 0 ? '+' : '';
        value = `${sign}${value.toFixed(2)}%`;
    }
    return value;
}

/** Build a full <tr> for one stock given the header columns. */
function orbRowHTML(row, headerColumns) {
    const hasAction = headerColumns.includes('Action');
    const values = [];
    headerColumns.forEach(col => { if (col !== 'Action') values.push(orbCellValue(row, col)); });
    const symbol = row.Symbol || row.symbol || 'Unknown';
    return `<tr>
        ${values.map((val, vi) => {
            const colName = headerColumns[vi];
            if (colName === 'Symbol') {
                return `<td class="symbol-cell" onclick="copySymbol('${symbol}', this)" title="Click to copy">${val}</td>`;
            }
            return `<td>${val}</td>`;
        }).join('')}
        ${hasAction ? `<td>
            <button class="btn-place-order btn-sm" onclick="placeOrder('${symbol}')" ${autoBuyEnabled ? 'disabled' : ''}>
                Place Order
            </button>
        </td>` : ''}
    </tr>`;
}

let currentQuickFilter = 'all';

function setQuickFilter(filterName) {
    currentQuickFilter = filterName;
    document.querySelectorAll('.quick-tab').forEach(tab => {
        const isMatch = (filterName === 'all' && tab.id === 'tabAll') ||
                        (filterName === 'breakout' && tab.id === 'tabBreakout') ||
                        (filterName === 'near_high' && tab.id === 'tabNearHigh') ||
                        (filterName === 'above_ema' && tab.id === 'tabAboveEma') ||
                        (filterName === 'inside915' && tab.id === 'tabInside915') ||
                        (filterName === 'extra_vol' && tab.id === 'tabExtraVol');
        tab.classList.toggle('active', isMatch);
    });

    const summary = document.getElementById('filterSummaryText');
    if (summary) {
        if (filterName === 'breakout') summary.textContent = '🚀 Showing Breakout candidates (Live price crossed/tested at or above 1st candle High)';
        else if (filterName === 'near_high') summary.textContent = '🎯 Showing Near High watchlist (Open within ±2% of yesterday\'s high)';
        else if (filterName === 'above_ema') summary.textContent = '📈 Showing stocks whose price is above the 200 EMA';
        else if (filterName === 'inside915') summary.textContent = '📐 Showing stocks consolidated inside opening 9:15 candle';
        else if (filterName === 'extra_vol') summary.textContent = '🔥 Showing stocks with 1st 15m candle volume HIGHEST across the last 3 days';
        else summary.textContent = 'Showing all scanned stocks sorted by CHG% (highest to lowest)';
    }

    if (lastAdvanceOrbData && typeof renderStrategyData === 'function') {
        renderStrategyData(lastAdvanceOrbData);
    }
}

function updateQuickFilterCounts(allRows) {
    if (!Array.isArray(allRows)) return;
    let breakoutCount = 0;
    let nearHighCount = 0;
    let aboveEmaCount = 0;
    let inside915Count = 0;
    let extraVolCount = 0;

    for (const r of allRows) {
        const price = parseFloat(r.Price ?? r.price);
        const high = parseFloat(r.high915 ?? r['1st High'] ?? r.yesterday_high);
        const ema = parseFloat(r.ema ?? r['200 EMA']);

        // Breakout
        if ((Number.isFinite(price) && Number.isFinite(high) && high > 0 && price >= (high * 0.99)) || r.Breakout === 'Confirmed') {
            breakoutCount++;
        }
        // Near high: If price is above prev day high -> pass. If below -> check distance within 2%.
        let isNearHighRow = r.near_high === true;
        if (!isNearHighRow) {
            const yh = parseFloat(r.yesterday_high ?? r['Prev High'] ?? r.high);
            if (Number.isFinite(price) && Number.isFinite(yh) && yh > 0) {
                isNearHighRow = price >= yh ? true : ((yh - price) / yh <= 0.02);
            }
        }
        if (isNearHighRow) {
            nearHighCount++;
        }

        // Above EMA
        if (r.above_ema === true) {
            aboveEmaCount++;
        } else {
            const c = parseFloat(r.close915 ?? r.Price ?? r.price);
            if (Number.isFinite(c) && Number.isFinite(ema) && ema > 0 && c >= ema) aboveEmaCount++;
        }
        // Inside 9:15
        if (r.inside_915 === true || r['Inside 9:15'] === 'Yes') {
            inside915Count++;
        }
        // Extra 15m Vol: Only count if first 15m candle volume exceeded the 3-day max
        const extra = parseFloat(r.extra_15m_vol ?? r['Extra 15m Vol']);
        if (r.is_15m_highest === true && Number.isFinite(extra) && extra > 0) {
            extraVolCount++;
        }

    }

    const cAll = document.getElementById('countAll');
    const cBreakout = document.getElementById('countBreakout');
    const cNearHigh = document.getElementById('countNearHigh');
    const cAboveEma = document.getElementById('countAboveEma');
    const cInside915 = document.getElementById('countInside915');
    const cExtraVol = document.getElementById('countExtraVol');

    if (cAll) cAll.textContent = allRows.length;
    if (cBreakout) cBreakout.textContent = breakoutCount;
    if (cNearHigh) cNearHigh.textContent = nearHighCount;
    if (cAboveEma) cAboveEma.textContent = aboveEmaCount;
    if (cInside915) cInside915.textContent = inside915Count;
    if (cExtraVol) cExtraVol.textContent = extraVolCount;
}

/** Unified row filter evaluates quick tabs and active strategy toggles */
function orbUnifiedFilter(r) {
    const price = parseFloat(r.Price ?? r.price);
    const high = parseFloat(r.high915 ?? r['1st High'] ?? r.yesterday_high);
    const ema = parseFloat(r.ema ?? r['200 EMA']);

    const isBreakout = (Number.isFinite(price) && Number.isFinite(high) && high > 0)
        ? (price >= (high * 0.99))
        : (r.Breakout === 'Confirmed' || r.Breakout === 'Forming');

    let isNearHigh = r.near_high === true;
    if (!isNearHigh) {
        const yh = parseFloat(r.yesterday_high ?? r['Prev High'] ?? r.high);
        if (Number.isFinite(price) && Number.isFinite(yh) && yh > 0) {
            isNearHigh = price >= yh ? true : ((yh - price) / yh <= 0.02);
        }
    }


    let isAboveEma = r.above_ema === true;
    if (!isAboveEma) {
        const c = parseFloat(r.close915 ?? r.Price ?? r.price);
        if (Number.isFinite(c) && Number.isFinite(ema) && ema > 0 && c >= ema) {
            isAboveEma = true;
        }
    }

    const isInside915 = r.inside_915 === true || r['Inside 9:15'] === 'Yes';
    const extra = parseFloat(r.extra_15m_vol ?? r['Extra 15m Vol']);
    const isExtraVol = r.is_15m_highest === true && Number.isFinite(extra) && extra > 0;


    // 1. Check active Quick Tab filter
    if (currentQuickFilter === 'breakout' && !isBreakout) return false;
    if (currentQuickFilter === 'near_high' && !isNearHigh) return false;
    if (currentQuickFilter === 'above_ema' && !isAboveEma) return false;
    if (currentQuickFilter === 'inside915' && !isInside915) return false;
    if (currentQuickFilter === 'extra_vol' && !isExtraVol) return false;

    // 2. Check active top bar toggles
    if (nearHighEnabled && !isNearHigh) return false;
    if (aboveEmaEnabled && !isAboveEma) return false;
    if (_inside915Only && !isInside915) return false;

    return true;
}

function getChgValue(r) {
    const raw = r['CHG%'] ?? r.change_pct ?? r.change ?? r['chg%'] ?? r.chg;
    const num = parseFloat(raw);
    return Number.isFinite(num) ? num : -999999;
}

function exportScreenerCSV() {
    const table = document.querySelector('.table-modern');
    if (!table) return;
    const rows = Array.from(table.querySelectorAll('tr'));
    if (!rows.length) {
        showToast('⚠️ No Data', 'No stocks to export');
        return;
    }
    const csvContent = rows.map(r => {
        const cells = Array.from(r.querySelectorAll('th, td'));
        return cells.map(c => `"${c.textContent.trim().replace(/"/g, '""')}"`).join(',');
    }).join('\n');
    
    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `screener_${currentQuickFilter}_${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('📥 Exported', 'Screener data saved as CSV');
}

// ================================================================
function renderStrategyData(result) {
    const strategyId = document.getElementById('strategySelect').value;
    const strategy = STRATEGIES[strategyId];
    if (!strategy) return;

    let rawData = result.data ? [...result.data] : [];
    updateQuickFilterCounts(rawData);

    // Filter rows according to active quick tab and toggles
    let data = (strategyId === 'advanceorb') ? rawData.filter(orbUnifiedFilter) : rawData;

    // Sort table by CHG% descending (highest to lowest)
    data.sort((a, b) => getChgValue(b) - getChgValue(a));

    // Standard columns for Advance ORB
    const UNIFIED_ORB_COLUMNS = [
        'Symbol',
        'Price',
        'CHG%',
        'Signal',
        'Extra 15m Vol',
        '200 EMA',
        '1st High',
        '1st Low',
        '1st Range%',
        'Inside 9:15',
        'GAP%',
        'Volume',
        'RELVOL',
        'Sector',
        'MaxQty',
        'Action'
    ];

    const headerColumns = (strategyId === 'advanceorb') ? UNIFIED_ORB_COLUMNS : [...(result.columns || strategy.columns || []), 'Action'];

    // Market-closed banner
    const _banner = document.getElementById('marketClosedBanner');
    if (_banner) {
        const _closed = result.market_closed === true;
        _banner.style.display = _closed ? '' : 'none';
        const _d = document.getElementById('marketClosedDate');
        if (_d) _d.textContent = result.reference_date || '';
    }

    // Update table headers
    const thead = document.getElementById('screenerHead');
    if (thead) thead.innerHTML = `<tr>${headerColumns.map(col => `<th>${col}</th>`).join('')}</tr>`;

    // Update table rows
    const tbody = document.getElementById('screenerBody');
    if (tbody) {
        if (data.length === 0) {
            tbody.innerHTML = `<tr><td colspan="${headerColumns.length}" style="text-align:center;padding:40px;color:var(--text-muted);">No stocks matching the selected filter criteria.</td></tr>`;
        } else {
            tbody.innerHTML = data.map(row => orbRowHTML(row, headerColumns)).join('');
        }
    }

    const countText = `Showing ${data.length} of ${rawData.length} stocks`;
    const sc = document.getElementById('screenerCount');
    if (sc) sc.textContent = countText;

    updatePlaceOrderButtons();
}

async function fetchBigPlayers() {
    try {
        const budget = _readBudget();
        const parts = _readParts();
        const res = await fetch(`/api/strategies/bigplayers?budget=${budget}&parts=${parts}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return await res.json();
    } catch (e) {
        console.warn('BigPlayers fetch error:', e);
        return null;
    }
}

function renderBigPlayersData(data) {
    if (!data) return;
    renderStrategyData(data);
}

let _bpAutoTimer = null;
function startBigPlayersAutoRefresh() {
    stopBigPlayersAutoRefresh();
    _bpAutoTimer = setInterval(async () => {
        const strategyId = document.getElementById('strategySelect')?.value;
        if (strategyId === 'bigplayers') {
            const data = await fetchBigPlayers();
            if (data) {
                window.lastBigPlayersData = data;
                renderBigPlayersData(data);
            }
        }
    }, 5000);
}

function stopBigPlayersAutoRefresh() {
    if (_bpAutoTimer) {
        clearInterval(_bpAutoTimer);
        _bpAutoTimer = null;
    }
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
        const thead = document.getElementById('screenerHead');
        const SIGNAL_SCAN_DROP = ['Sector', '1st Range%', 'Inside 9:15', 'MaxQty'];
        const columns = [...strategy.columns].filter(c => !SIGNAL_SCAN_DROP.includes(c));
        if (thead) thead.innerHTML = `<tr>${columns.map(col => `<th>${col}</th>`).join('')}</tr>`;
        if (tbody) tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;">🔎 Filtering best-performing stocks…</td></tr>`;
        const countEl = document.getElementById('screenerCount');
        if (countEl) countEl.textContent = 'Loading...';

        // Show Advance ORB toggles, hide Big Players-specific toggles
        const autoBuyEl = document.getElementById('autoBuyWrap');
        const nearHighEl = document.getElementById('nearHighWrap');
        const aboveEmaEl = document.getElementById('aboveEmaWrap');
        const inside915El = document.getElementById('inside915Wrap');
        const calcQtyEl = document.getElementById('calcQtyWrap');
        if (autoBuyEl) autoBuyEl.style.display = '';
        if (nearHighEl) nearHighEl.style.display = '';
        if (aboveEmaEl) aboveEmaEl.style.display = '';
        if (inside915El) inside915El.style.display = '';
        if (calcQtyEl) calcQtyEl.style.display = '';
        const nw = document.getElementById('newLowFilterWrap');
        if (nw) nw.style.display = 'none';
        const bpab = document.getElementById('bpAutoBuyWrap');
        if (bpab) bpab.style.display = 'none';

        const result = await fetchAdvanceORB();
        if (result) {
            lastAdvanceOrbData = result;
            renderStrategyData(result);
            startAdvanceOrbAutoRefresh();
            // Stop Big Players refresh if running
            if (typeof stopBigPlayersAutoRefresh === 'function') stopBigPlayersAutoRefresh();
        } else {
            if (tbody) tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--color-danger);">❌ Failed to load data. Please try again.</td></tr>`;
            if (countEl) countEl.textContent = '0 stocks';
        }
        return;
    }

    // ============================================================
    // CASE 2: BIG PLAYERS (API call)
    // ============================================================
    if (strategyId === 'bigplayers') {
        const tbody = document.getElementById('screenerBody');
        const thead = document.getElementById('screenerHead');
        const columns = [...strategy.columns];
        columns.push('Action');
        if (thead) thead.innerHTML = `<tr>${columns.map(col => `<th>${col}</th>`).join('')}</tr>`;
        if (tbody) tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;">🏢 Fetching Big Players data…</td></tr>`;
        const countEl = document.getElementById('screenerCount');
        if (countEl) countEl.textContent = 'Loading...';

        // Hide Advance ORB toggles, show Big Players-specific toggles
        const orbab = document.getElementById('autoBuyWrap');
        if (orbab) orbab.style.display = 'none';
        const nearHighEl = document.getElementById('nearHighWrap');
        if (nearHighEl) nearHighEl.style.display = 'none';
        const aboveEmaEl = document.getElementById('aboveEmaWrap');
        if (aboveEmaEl) aboveEmaEl.style.display = 'none';
        const inside915El = document.getElementById('inside915Wrap');
        if (inside915El) inside915El.style.display = 'none';
        const calcQtyEl = document.getElementById('calcQtyWrap');
        if (calcQtyEl) calcQtyEl.style.display = 'none';
        const nw = document.getElementById('newLowFilterWrap');
        if (nw) nw.style.display = '';
        const bpab = document.getElementById('bpAutoBuyWrap');
        if (bpab) bpab.style.display = '';

        // Call Big Players API
        const result = await fetchBigPlayers();
        if (result) {
            window.lastBigPlayersData = result;
            renderBigPlayersData(result);
            startBigPlayersAutoRefresh();
            // Stop Advance ORB refresh if running
            if (typeof stopAdvanceOrbAutoRefresh === 'function') stopAdvanceOrbAutoRefresh();
        } else {
            if (tbody) tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--color-danger);">❌ Failed to load Big Players data</td></tr>`;
            if (countEl) countEl.textContent = '0 stocks';
        }
        return;
    }


    // ============================================================
    // CASE 3: SMARTMONEY (Hardcoded data)
    // ============================================================
    // Update table headers
    const thead = document.getElementById('screenerHead');
    const columns = [...strategy.columns];
    columns.push('Action');
    if (thead) thead.innerHTML = `<tr>${columns.map(col => `<th>${col}</th>`).join('')}</tr>`;

    // Update table rows
    const tbody = document.getElementById('screenerBody');
    if (strategy.data.length === 0) {
        if (tbody) tbody.innerHTML = `<tr><td colspan="${columns.length}" style="text-align:center;padding:40px;color:var(--text-muted);">No stocks found for this strategy.</td></tr>`;
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
                else if (col === 'Max Qty') value = displayRow.maxQty || displayRow.MaxQty || '';
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
                else if (col === 'MaxQty') value = displayRow.maxQty || displayRow.MaxQty || '';
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
        // Reset the placed-symbols tracker — next time auto-buy is
        // turned ON it starts fresh (new day / new session).
        _autoBuyPlacedSymbols = new Set();
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
// Track which symbols have already been auto-bought today so the
// 30-second re-evaluation doesn't place duplicate orders.
let _autoBuyPlacedSymbols = new Set();

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
        .filter(r => r && r.Symbol && Number(r.MaxQty) > 0)
        // Skip symbols already auto-bought this session — prevents
        // duplicate orders on the 30-second re-evaluation cycle once
        // a stock has been purchased.
        .filter(r => !_autoBuyPlacedSymbols.has(r.Symbol));

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
    // ADDITIONAL CHECK — 2nd Candle (9:20) Confirmation
    // -----------------------------------------------------------------
    // The 9:20 (2nd 5-min) candle must have closed inside the 9:15
    // candle range before we buy. This ensures the stock didn't reject
    // the 9:15 range immediately — a candle that closes outside means
    // the breakout already failed or is false.
    // Because the 9:20 candle data comes from yfinance (which can be
    // slightly delayed), we also skip rows where inside_915 is null
    // (data not yet available) to avoid buying on incomplete info.
    // -----------------------------------------------------------------
    const bandFiltered = eligible.filter(row => {
        const high915 = parseFloat(row.high915);
        const price = parseFloat(row.Price ?? row.price);
        // Skip rows missing either anchor (zero / NaN) — can't decide.
        if (!Number.isFinite(high915) || high915 <= 0) return false;
        if (!Number.isFinite(price) || price <= 0) return false;
        // 2nd candle must have closed inside 9:15 range before buying
        if (row.inside_915 !== true) return false;
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
            const price = parseFloat(row.Price ?? row.price);
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
        const price = parseFloat(row.Price ?? row.price);
        if (!Number.isFinite(ema)) return false;          // yfinance missed
        if (!Number.isFinite(price) || price <= 0) return false;
        return price > ema;
    });

    if (aboveEma.length === 0) {
        const expectedBand = `+${AUTO_BUY_MIN_MOVE_ABOVE_915_PCT}–+${AUTO_BUY_MAX_MOVE_ABOVE_915_PCT}%`;
        const emaProbes = bandFiltered.slice(0, 6).map(row => {
            const ema   = parseFloat(row.ema);
            const price = parseFloat(row.Price ?? row.price);
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
const AUTO_REFRESH_MS = 2500;

async function fetchAdvanceORBRefresh(silent = true) {
    if (!lastAdvanceOrbData || !lastAdvanceOrbData.data || lastAdvanceOrbData.data.length === 0) {
        return;
    }

    // ── Candle-ready gate ────────────────────────────────────────────
    // When candle_data_available is false the lightweight refresh
    // endpoint can't supply 1st-candle columns. Instead, fire a full
    // re-fetch so the table gets candle data the moment slot-0
    // completes (at ~9:20 IST). Once the re-fetch returns with
    // candle_data_available === true the banner hides automatically
    // via renderStrategyData.
    if (lastAdvanceOrbData.candle_data_available === false) {
        try {
            const result = await fetchAdvanceORB();
            if (result) {
                lastAdvanceOrbData = result;
                const strategyId = document.getElementById('strategySelect')?.value;
                const activePage = document.querySelector('.page.active');
                const onScreener = activePage && activePage.id === 'page-screener';
                if (onScreener && strategyId === 'advanceorb') {
                    renderStrategyData(result);
                    if (result.candle_data_available) {
                        showToast('📊 Candle Data Ready', '9:15 candle data is now available — table updated.');
                    }
                }
            }
        } catch (e) {
            console.warn('[candle-poller] full re-fetch failed:', e);
        }
        return;
    }

    const symbols = lastAdvanceOrbData.data.map(r => r.Symbol).filter(Boolean);
    if (symbols.length === 0) return;
    try {
        const response = await fetch('/api/strategies/advanceorb/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tickers: symbols, timeframe: orbTimeframe }),
            cache: 'no-store'
        });
        if (!response.ok) return;
        const result = await response.json();

        const refreshedList = Array.isArray(result?.refreshed) ? result.refreshed : [];
        if (refreshedList.length === 0) return;
        const bySymbol = {};
        for (const r of refreshedList) {
            if (r && r.Symbol) bySymbol[r.Symbol] = r;
        }
        // The refresh endpoint re-checks the 9:15 candle eligibility and
        // omits symbols that no longer qualify. Remove those from the
        // local dataset so the table stays in sync with the new list.
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
            // 9:20 candle confirmation — auto-updates every 30 seconds
            // without a full page reload. Once the 9:20 candle closes
            // inside the 9:15 range, inside_915 flips to true and
            // auto-buy can proceed.
            if (typeof updated.inside_915 === 'boolean') row.inside_915 = updated.inside_915;
            if (typeof updated.close920 === 'number') row.close920 = updated.close920;
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
                // Flash Price cells briefly so the user can see the
                // refresh actually updated prices on screen.
                setTimeout(() => {
                    const rows = document.querySelectorAll('#screenerBody tr');
                    const headers = document.querySelectorAll('#screenerHead th');
                    let priceIdx = -1;
                    for (let i = 0; i < headers.length; i++) {
                        if (headers[i].textContent.trim() === 'Price') { priceIdx = i; break; }
                    }
                    if (priceIdx >= 0) {
                        for (const tr of rows) {
                            const cell = tr.querySelectorAll('td')[priceIdx];
                            if (cell) {
                                cell.style.transition = 'background 0.15s';
                                cell.style.background = 'rgba(34,197,94,0.2)';
                                setTimeout(() => { cell.style.background = ''; }, 400);
                            }
                        }
                    }
                }, 10);
                if (!silent) showToast('🔄 Refreshed', `${touched} stocks updated`);
                // Auto-buy re-evaluation: if auto-buy is ON, re-scan
                // candidates every refresh cycle. This catches stocks
                // whose inside_915 just flipped to true (9:20 candle
                // closed inside 9:15 range) or whose price just entered
                // the breakout band. Without this, auto-buy only runs
                // once when toggled ON and would miss entries that
                // become eligible later.
                if (autoBuyEnabled) {
                    autoBuyAllStocks();
                }
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

// Throttled quick tab count update & re-evaluation
let _screenerRerenderAt = 0;
function maybeRerenderScreener() {
    const now = Date.now();
    if (now < _screenerRerenderAt) return;
    _screenerRerenderAt = now + 1500;
    const strategyId = document.getElementById('strategySelect')?.value;
    const activePage = document.querySelector('.page.active');
    const onScreener = activePage && activePage.id === 'page-screener';
    if (strategyId === 'advanceorb' && onScreener && lastAdvanceOrbData && lastAdvanceOrbData.data) {
        updateQuickFilterCounts(lastAdvanceOrbData.data);
    }
}

let _tickWatchdog = null;

function startLiveTickPoll() {
    stopLiveTickPoll();
    const url = '/api/market/live-ticks/stream';
    _tickEventSource = new EventSource(url);
    _tickEventSource.onmessage = function (ev) {
        // Reset watchdog on every message (heartbeat or data)
        _resetTickWatchdog();
        try {
            const data = JSON.parse(ev.data);
            if (data && data.ticks) {
                _lastTickPayload = data.ticks;
                const activePage = document.querySelector('.page.active');
                const onScreener = activePage && activePage.id === 'page-screener';
                const strategyId = document.getElementById('strategySelect')?.value;
                if (onScreener && (strategyId === 'advanceorb' || !strategyId)) {
                    _applyTicks(data.ticks);
                    maybeRerenderScreener();
                }
            }
        } catch (_) {}

    };
    _tickEventSource.onerror = function () {
        // EventSource auto-reconnects natively
    };
}

function _resetTickWatchdog() {
    if (_tickWatchdog) clearTimeout(_tickWatchdog);
    _tickWatchdog = setTimeout(() => {
        console.warn('[tick-watchdog] No message for 10s — reconnecting SSE');
        if (_tickEventSource) {
            try { _tickEventSource.close(); } catch (_) {}
            _tickEventSource = null;
        }
        startLiveTickPoll();
    }, 10000);
}

function stopLiveTickPoll() {
    if (_tickWatchdog) {
        clearTimeout(_tickWatchdog);
        _tickWatchdog = null;
    }
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
        const cell = _lookupRowCell(symbol, 'MaxQty', ['#screenerHead'], ['#screenerBody']);
        return Math.max(0, parseInt((cell || '').replace(/[^0-9-]/g, ''), 10) || 0);
    } catch (e) { console.warn('qty lookup failed for', symbol, e); }
    return 0;
}

/** Find a named column's cell text for a symbol. */
function _lookupRowCell(symbol, colName, headSelectors, bodySelectors) {
    for (let t = 0; t < headSelectors.length; t++) {
        const headers = document.querySelectorAll(headSelectors[t] + ' th');
        const tr = Array.from(document.querySelectorAll(bodySelectors[t] + ' tr')).find(r => {
            const first = r.querySelector('td');
            return first && first.textContent.trim() === symbol;
        });
        if (!tr) continue;
        const cells = tr.querySelectorAll('td');
        for (let i = 0; i < headers.length && i < cells.length; i++) {
            if (headers[i].textContent.trim() === colName) return cells[i].textContent || '';
        }
    }
    // Column not rendered in table → read from backing data
    const row = _orderRowForSymbol(symbol);
    if (row) {
        if (colName === 'MaxQty') return row.MaxQty != null ? String(row.MaxQty) : '';
        if (colName === 'Price') return row.Price != null ? String(row.Price) : '';
    }
    return '';
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
function _parsePctStr(s) {
    if (s == null) return 0;
    const m = String(s).match(/-?\d+(\.\d+)?/);
    return m ? parseFloat(m[0]) : 0;
}

// Locate the row's Price cell
function _lookupRowPrice(symbol) {
    try {
        const cell = _lookupRowCell(symbol, 'Price', ['#screenerHead'], ['#screenerBody']);
        return parseFloat((cell || '').replace(/[^0-9.]/g, '')) || 0;
    } catch (e) { console.warn('price lookup failed for', symbol, e); }
    return 0;
}

