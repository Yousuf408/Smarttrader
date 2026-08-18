// ================================================================
// TESTING PAGE — All subscribed stocks with live tick data
// ================================================================

let testingRefreshInterval = null;
let testingData = null;

function loadTestingData() {
    const tbody = document.getElementById('testingTableBody');
    const countEl = document.getElementById('testingCount');
    const updatedEl = document.getElementById('testingUpdated');
    const connEl = document.getElementById('testingConnectionStatus');

    fetch('/api/market/live-ticks')
        .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
        .then(data => {
            testingData = data;
            const ticks = data.ticks || {};
            const symbols = Object.keys(ticks);

            // Update connection status
            connEl.textContent = data.connected ? '🟢 Connected' : '🔴 Disconnected';
            connEl.style.color = data.connected ? 'var(--color-success)' : 'var(--color-danger)';

            // Filter to unique symbols (prefer non -EQ versions) — the API
            // returns both "COLPAL-EQ" and "COLPAL" aliases, so the raw key
            // count (~1400 for 727 stocks) is NOT the real stock count.
            const uniqueSymbols = [];
            const seen = new Set();
            for (const sym of symbols) {
                const base = sym.replace(/-EQ$/i, '');
                if (!seen.has(base)) {
                    seen.add(base);
                    uniqueSymbols.push(sym);
                }
            }

            // Update count & timestamp (deduped count == watchlist size)
            countEl.textContent = uniqueSymbols.length + ' stocks';
            updatedEl.textContent = new Date().toLocaleTimeString();

            if (uniqueSymbols.length === 0) {
                tbody.innerHTML = '<tr><td colspan="10" style="text-align:center;padding:40px;opacity:0.5;">No tick data available</td></tr>';
                return;
            }

            // Sort by change % descending (most gainers first)
            uniqueSymbols.sort((a, b) => {
                const ca = ticks[a]?.change_pct || 0;
                const cb = ticks[b]?.change_pct || 0;
                return cb - ca;
            });

            let html = '';
            uniqueSymbols.forEach((sym, idx) => {
                const t = ticks[sym];
                const ltp = t?.ltp || 0;
                const chg = t?.change_pct || 0;
                const open = t?.open || 0;
                const high = t?.high || 0;
                const low = t?.low || 0;
                const vol = t?.volume || 0;
                const ts = t?.timestamp || '';

                // Compute gap % = (LTP - Open) / Open * 100
                const gap = open > 0 ? ((ltp - open) / open * 100) : 0;

                const chgColor = chg >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                const chgIcon = chg >= 0 ? '▲' : '▼';

                // Format volume
                let volStr;
                if (vol >= 1_000_000) volStr = (vol / 1_000_000).toFixed(1) + 'M';
                else if (vol >= 1_000) volStr = (vol / 1_000).toFixed(1) + 'K';
                else volStr = vol.toString();

                const displaySym = sym.replace(/-EQ$/i, '');

                html += `<tr>
                    <td data-label="#" style="opacity:0.4;font-size:12px;">${idx + 1}</td>
                    <td data-label="Symbol"><strong>${displaySym}</strong></td>
                    <td data-label="LTP" style="font-weight:600;">${ltp.toFixed(2)}</td>
                    <td data-label="Change" style="color:${chgColor};font-weight:600;">${chgIcon} ${chg.toFixed(2)}%</td>
                    <td data-label="Open">${open.toFixed(2)}</td>
                    <td data-label="High" style="color:var(--color-success);">${high.toFixed(2)}</td>
                    <td data-label="Low" style="color:var(--color-danger);">${low.toFixed(2)}</td>
                    <td data-label="Volume">${volStr}</td>
                    <td data-label="Gap">${gap.toFixed(2)}%</td>
                    <td data-label="Time" style="font-size:11px;opacity:0.6;">${ts}</td>
                </tr>`;
            });

            tbody.innerHTML = html;
        })
        .catch(err => {
            console.error('Testing page fetch error:', err);
            tbody.innerHTML = `<tr><td colspan="10" style="text-align:center;padding:40px;opacity:0.5;">Error loading data: ${err.message}</td></tr>`;
        });
}

function startTestingAutoRefresh() {
    stopTestingAutoRefresh();
    loadTestingData();
    testingRefreshInterval = setInterval(loadTestingData, 2000);
}

function stopTestingAutoRefresh() {
    if (testingRefreshInterval) {
        clearInterval(testingRefreshInterval);
        testingRefreshInterval = null;
    }
}

let currentTestingTab = 'ticks';

function switchTestingTab(tab) {
    currentTestingTab = tab;
    const tabTicks = document.getElementById('testingTabTicks');
    const tabOhlc = document.getElementById('testingTabOhlc');
    const viewTicks = document.getElementById('testingViewTicks');
    const viewOhlc = document.getElementById('testingViewOhlc');
    const ticksMeta = document.getElementById('testingTicksMeta');

    if (tab === 'ohlc') {
        if (tabTicks) { tabTicks.className = 'btn btn-sm btn-outline'; }
        if (tabOhlc) { tabOhlc.className = 'btn btn-sm btn-primary'; }
        if (viewTicks) viewTicks.style.display = 'none';
        if (viewOhlc) viewOhlc.style.display = 'block';
        if (ticksMeta) ticksMeta.style.display = 'none';
        stopTestingAutoRefresh();
    } else {
        if (tabTicks) { tabTicks.className = 'btn btn-sm btn-primary'; }
        if (tabOhlc) { tabOhlc.className = 'btn btn-sm btn-outline'; }
        if (viewTicks) viewTicks.style.display = 'block';
        if (viewOhlc) viewOhlc.style.display = 'none';
        if (ticksMeta) ticksMeta.style.display = 'flex';
        startTestingAutoRefresh();
    }
}

// ================================================================
// NAVIGATION HOOK — main.js calls this when the testing page opens
// ================================================================
function loadTesting() {
    if (currentTestingTab === 'ticks') {
        startTestingAutoRefresh();
    }
}

// Also stop when leaving
document.addEventListener('visibilitychange', () => {
    if (document.getElementById('page-testing')?.classList.contains('active')) {
        if (!document.hidden) loadTestingData();
    }
});
