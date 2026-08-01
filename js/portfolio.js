// ================================================================
// INTRADAY PORTFOLIO — real broker data
// ================================================================

let portfolioData = { holdings: [], positions: [], funds: null };
let _portfolioSimInterval = null;

// ================================================================
// LOAD PORTFOLIO — fetch funds + holdings from broker
// ================================================================
async function loadPortfolio() {
    startSimulation();

    const [fundsRes, holdingsRes, positionsRes] = await Promise.all([
        fetch('/api/portfolio/funds').catch(() => null),
        fetch('/api/portfolio/holdings').catch(() => null),
        fetch('/api/portfolio/positions').catch(() => null),
    ]);

    const funds   = fundsRes    ? await fundsRes.json().catch(() => ({})) : {};
    const holdingsResData = holdingsRes ? await holdingsRes.json().catch(() => ({})) : {};
    const positionsResData = positionsRes ? await positionsRes.json().catch(() => ({})) : {};

    portfolioData.funds = funds;
    portfolioData.holdings = holdingsResData.data || [];
    portfolioData.positions = positionsResData.data || [];

    const isConnected = funds.success || holdingsResData.success || positionsResData.success;
    if (!isConnected) {
        renderEmptyPortfolio();
        return;
    }

    renderPortfolio(funds, portfolioData.holdings, portfolioData.positions);
}

// ================================================================
// RENDER — full portfolio with real broker data
// ================================================================
function renderPortfolio(funds, holdings, positions) {
    // --- Funds / Budget ---
    const availableCash = _extractAvailable(funds);
    const totalInvested = positions.reduce((s, p) => s + (_getVal(p, 'buyAmount') + _getVal(p, 'sellAmount')), 0);
    const totalMtmPnl = positions.reduce((s, p) => s + _getVal(p, 'mtm'), 0);
    const dayPnl = positions.reduce((s, p) => s + (_getVal(p, 'realizedPnl') + _getVal(p, 'unrealizedPnl')), 0);
    const dayPnlRaw = dayPnl || totalMtmPnl;

    const activePositions = positions.filter(p => _getQty(p) > 0);
    const totalValue = availableCash + totalInvested;

    // Stats row
    document.getElementById('portfolioStats').innerHTML = `
        <div class="stat-box">
            <div class="stat-icon">💰</div>
            <div class="label">Available</div>
            <div class="value">₹${numberFmt(availableCash)}</div>
            <div class="sub" style="color:var(--text-muted);font-size:9px;">
                <div class="progress-mini">
                    <div class="progress-fill" style="width:${totalValue > 0 ? Math.round(availableCash/totalValue*100) : 100}%;background:var(--gradient-brand);"></div>
                </div>
                ${totalValue > 0 ? Math.round(availableCash/totalValue*100) : 100}% free
            </div>
        </div>
        <div class="stat-box">
            <div class="stat-icon">📊</div>
            <div class="label">Invested</div>
            <div class="value">₹${numberFmt(totalInvested)}</div>
            <div class="sub" style="color:var(--text-muted);font-size:9px;">
                <div class="progress-mini">
                    <div class="progress-fill" style="width:${totalValue > 0 ? Math.round(totalInvested/totalValue*100) : 0}%;background:var(--color-warning);"></div>
                </div>
                ${activePositions.length} position(s)
            </div>
        </div>
        <div class="stat-box">
            <div class="stat-icon">💵</div>
            <div class="label">Margin Used</div>
            <div class="value">₹${numberFmt(_extractMarginUsed(funds))}</div>
            <div class="sub" style="color:var(--text-muted);font-size:9px;">Intraday</div>
        </div>
        <div class="stat-box" style="border-left:3px solid ${dayPnlRaw >= 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
            <div class="stat-icon">📈</div>
            <div class="label">Day P&L</div>
            <div class="value" style="color:${dayPnlRaw >= 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${dayPnlRaw >= 0 ? '+' : ''}₹${numberFmt(Math.abs(dayPnlRaw))}
            </div>
            <div class="sub ${dayPnlRaw >= 0 ? 'green' : 'red'}">
                ${dayPnlRaw >= 0 ? '↑' : '↓'}
                <span style="font-weight:400;color:var(--text-muted);margin-left:4px;font-size:9px;">
                    ${activePositions.length} active · ${funds.broker || '—'}
                </span>
            </div>
        </div>
    `;

    // Intraday Metrics
    renderIntradayMetrics(activePositions, positions, dayPnlRaw, availableCash);

    // Holdings / Positions table
    renderPortfolioTable(positions, holdings);

    // Footer
    const countEl = document.getElementById('holdingsCount');
    if (countEl) {
        const activeCount = activePositions.length;
        const totalCount = positions.length;
        countEl.textContent = `${totalCount > 0 ? `Showing ${totalCount} position(s)` : 'No positions'} ${activeCount > 0 ? `(${activeCount} active)` : ''}`;
    }

    const now = new Date();
    const lu = document.getElementById('lastUpdated');
    if (lu) lu.textContent = `Last updated: ${now.toLocaleTimeString()}`;

    const gainers = activePositions.filter(p => _getPnl(p) > 0).length;
    const losers = activePositions.filter(p => _getPnl(p) < 0).length;
    document.getElementById('gainersCount').textContent = `▲ ${gainers}`;
    document.getElementById('losersCount').textContent = `▼ ${losers}`;
}

// ================================================================
// EMPTY STATE — no broker connected
// ================================================================
function renderEmptyPortfolio() {
    document.getElementById('portfolioStats').innerHTML = `
        <div class="stat-box" style="grid-column:1/-1;text-align:center;padding:20px;">
            <div class="stat-icon" style="font-size:24px;">🔌</div>
            <div class="label">No Broker Connected</div>
            <div class="value" style="font-size:14px;color:var(--text-muted);">Connect your broker in Settings</div>
            <div class="sub" style="margin-top:6px;">
                <button class="btn btn-primary btn-sm" onclick="navigateTo('settings')">⚙️ Go to Settings</button>
            </div>
        </div>
    `;

    document.getElementById('intradayMetrics').innerHTML = `
        <div class="metric-card" style="grid-column:1/-1;text-align:center;padding:30px;">
            <div class="metric-label" style="font-size:13px;color:var(--text-muted);">No data — connect your broker to see intraday metrics</div>
        </div>
    `;

    document.getElementById('holdingsTable').innerHTML = `
        <div style="text-align:center;padding:40px;color:var(--text-muted);font-size:13px;">
            <div style="font-size:32px;margin-bottom:8px;">📭</div>
            <p>No positions to display</p>
            <p style="font-size:11px;margin-top:4px;">Connect your broker to view live holdings</p>
        </div>
    `;

    document.getElementById('gainersCount').textContent = '▲ 0';
    document.getElementById('losersCount').textContent = '▼ 0';
    const countEl = document.getElementById('holdingsCount');
    if (countEl) countEl.textContent = 'No positions';
    const lu = document.getElementById('lastUpdated');
    if (lu) lu.textContent = `Last updated: ${new Date().toLocaleTimeString()}`;
}

// ================================================================
// INTRADAY METRICS
// ================================================================
function renderIntradayMetrics(activePositions, allPositions, dayPnl, availableCash) {
    const winRate = activePositions.length > 0
        ? Math.round(activePositions.filter(p => _getPnl(p) > 0).length / activePositions.length * 100)
        : 0;
    const avgReturn = activePositions.length > 0
        ? activePositions.reduce((s, p) => s + _getPnl(p), 0) / activePositions.length
        : 0;
    const marginUsed = _extractMarginUsed(portfolioData.funds);
    const pnlOnMargin = marginUsed > 0 ? (dayPnl / marginUsed * 100) : 0;

    let best = null, bestPnl = -Infinity;
    let worst = null, worstPnl = Infinity;
    activePositions.forEach(p => {
        const pnl = _getPnl(p);
        if (pnl > bestPnl) { best = p; bestPnl = pnl; }
        if (pnl < worstPnl) { worst = p; worstPnl = pnl; }
    });

    const el = document.getElementById('intradayMetrics');
    if (!el) return;

    el.innerHTML = `
        <div class="metric-card" style="border-left:2px solid var(--brand-start);">
            <div class="metric-label">💰 Available</div>
            <div class="metric-value" style="color:var(--color-success);font-size:15px;">
                ₹${numberFmt(availableCash)}
            </div>
            <div class="metric-sub">Trading budget</div>
        </div>
        <div class="metric-card" style="border-left:2px solid var(--color-warning);">
            <div class="metric-label">📈 Margin P&L</div>
            <div class="metric-value" style="color:${pnlOnMargin > 0 ? 'var(--color-success)' : pnlOnMargin < 0 ? 'var(--color-danger)' : 'var(--text-muted)'};">
                ${pnlOnMargin > 0 ? '+' : ''}${pnlOnMargin.toFixed(2)}%
            </div>
            <div class="metric-sub">On margin used</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">🏆 Best</div>
            <div class="metric-value" style="color:var(--color-success);font-size:15px;">
                ${best ? _getSym(best) : '—'}
            </div>
            <div class="metric-sub">${best ? '+' + numberFmt(bestPnl) : ''}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">📉 Worst</div>
            <div class="metric-value" style="color:var(--color-danger);font-size:15px;">
                ${worst ? _getSym(worst) : '—'}
            </div>
            <div class="metric-sub">${worst ? numberFmt(worstPnl) : ''}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">📊 Win Rate</div>
            <div class="metric-value" style="color:${winRate >= 50 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${winRate}%
            </div>
            <div class="metric-sub">${activePositions.filter(p => _getPnl(p) > 0).length}/${activePositions.length}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">📊 Avg Return</div>
            <div class="metric-value" style="color:${avgReturn > 0 ? 'var(--color-success)' : avgReturn < 0 ? 'var(--color-danger)' : 'var(--text-muted)'};">
                ${avgReturn > 0 ? '+' : ''}₹${numberFmt(Math.abs(avgReturn))}
            </div>
            <div class="metric-sub">Per position</div>
        </div>
    `;
}

// ================================================================
// PORTFOLIO TABLE
// ================================================================
function renderPortfolioTable(positions, holdings) {
    const el = document.getElementById('holdingsTable');
    if (!el) return;

    // Build a unified list from positions + holdings
    const items = _buildUnifiedRows(positions, holdings);

    if (items.length === 0) {
        el.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:13px;"><div style="font-size:32px;margin-bottom:8px;">📭</div><p>No positions to display</p></div>';
        return;
    }

    el.innerHTML = `
        <table class="table-modern">
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Product</th>
                    <th>Qty</th>
                    <th>Buy Avg</th>
                    <th>LTP</th>
                    <th>P&L</th>
                    <th>P&L %</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${items.map((item) => {
                    const isGainer = item.pnl >= 0;
                    const qty = item.qty;
                    const displayQty = qty !== 0 ? qty : '—';

                    return `
                        <tr style="${!isGainer && qty !== 0 ? 'background:rgba(225,112,85,0.02);' : ''}">
                            <td>
                                <div style="display:flex;align-items:center;gap:4px;">
                                    <span class="status-dot ${isGainer ? 'green' : 'red'}"></span>
                                    <span class="symbol-highlight">${item.symbol}</span>
                                    ${item.isSold ? '<span class="sold-badge">CLOSED</span>' : ''}
                                </div>
                            </td>
                            <td><span class="sector-tag ${isGainer ? 'positive' : 'negative'}">${item.product || 'INTRADAY'}</span></td>
                            <td>${displayQty}</td>
                            <td style="font-weight:600;font-size:11px;">${item.buyAvg > 0 ? '₹' + numberFmt(item.buyAvg) : '—'}</td>
                            <td style="font-weight:600;font-size:11px;">${item.ltp > 0 ? '₹' + numberFmt(item.ltp) : '—'}</td>
                            <td style="color:${item.pnl > 0 ? 'var(--color-success)' : item.pnl < 0 ? 'var(--color-danger)' : 'var(--text-muted)'};font-weight:700;font-size:11px;">
                                ${item.pnl > 0 ? '+' : ''}${item.pnl !== 0 ? '₹' + numberFmt(Math.abs(item.pnl)) : '—'}
                            </td>
                            <td>
                                <span style="color:${item.pnlPct > 0 ? 'var(--color-success)' : item.pnlPct < 0 ? 'var(--color-danger)' : 'var(--text-muted)'};font-weight:700;font-size:11px;">
                                    ${item.pnlPct !== 0 ? (item.pnlPct > 0 ? '+' : '') + item.pnlPct.toFixed(2) + '%' : '—'}
                                </span>
                            </td>
                            <td>
                                <div class="action-group">
                                    ${qty > 0 ? `
                                        <button class="action-btn action-btn-sell" onclick="showToast('📤','Place sell order for ${item.symbol}')">Sell</button>
                                        <button class="action-btn action-btn-sl" onclick="showToast('⛔','Set SL for ${item.symbol}')">SL</button>
                                    ` : `
                                        <span style="font-size:8px;color:var(--text-muted);">—</span>
                                    `}
                                    <button class="action-btn action-btn-book" onclick='showToast("📊","${item.symbol} chart")'>📊</button>
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
    `;
}

// ================================================================
// HELPERS — normalize broker data
// ================================================================

function _getVal(obj, key) {
    if (!obj || typeof obj !== 'object') return 0;
    const v = obj[key];
    return (v != null && !isNaN(Number(v))) ? Number(v) : 0;
}

function _getQty(p) {
    return _getVal(p, 'netQty') || _getVal(p, 'quantity') || _getVal(p, 'buyQty') || _getVal(p, 'sellQty') || 0;
}

function _getSym(p) {
    return p.tradingSymbol || p.symbol || p.TradingSymbol || '?';
}

function _getPnl(p) {
    return _getVal(p, 'mtm') || (_getVal(p, 'unrealizedPnl')) || 0;
}

function _buildUnifiedRows(positions, holdings) {
    // Merge positions (intraday + carry-forward) with holdings
    const seen = new Set();
    const rows = [];

    // First: positions (they have live P&L)
    for (const p of positions) {
        const sym = _getSym(p);
        if (!sym || sym === '?') continue;
        seen.add(sym);
        const qty = _getQty(p);
        const buyAvg = _getVal(p, 'buyAvg') || _getVal(p, 'buyPrice') || _getVal(p, 'entryPrice') || 0;
        const ltp = _getVal(p, 'ltp') || _getVal(p, 'marketValue') || _getVal(p, 'currentPrice') || _getVal(p, 'closePrice') || 0;
        const pnl = _getPnl(p);
        const pnlPct = buyAvg > 0 ? (pnl / (buyAvg * Math.abs(qty))) * 100 : 0;
        rows.push({
            symbol: sym,
            qty: qty,
            buyAvg: buyAvg,
            ltp: ltp,
            pnl: pnl,
            pnlPct: pnlPct,
            product: _getVal(p, 'productType') ? String(p.productType) : 'INTRADAY',
            isSold: false,
        });
    }

    // Second: holdings not in positions (delivery holdings)
    for (const h of holdings) {
        const sym = h.tradingSymbol || h.symbol || h.TradingSymbol || '';
        if (!sym || seen.has(sym)) continue;
        seen.add(sym);
        const qty = _getVal(h, 'totalQty') || _getVal(h, 'quantity') || _getVal(h, 'holdingQty') || 0;
        const buyAvg = _getVal(h, 'averagePrice') || _getVal(h, 'buyPrice') || 0;
        const ltp = _getVal(h, 'ltp') || _getVal(h, 'marketValue') || _getVal(h, 'currentPrice') || 0;
        const pnl = _getVal(h, 'pnl') || (ltp > 0 && buyAvg > 0 ? (ltp - buyAvg) * qty : 0);
        const pnlPct = buyAvg > 0 ? ((ltp - buyAvg) / buyAvg) * 100 : 0;
        rows.push({
            symbol: sym,
            qty: qty,
            buyAvg: buyAvg,
            ltp: ltp,
            pnl: pnl,
            pnlPct: pnlPct,
            product: 'CNC',
            isSold: qty === 0,
        });
    }

    return rows;
}

function _extractAvailable(funds) {
    if (!funds || !funds.data) return 0;
    const d = funds.data;
    return _getVal(d, 'availabelBalance')          // Dhan (note: Dhan's own spelling)
        || _getVal(d, 'availableBalance')           // generic
        || _getVal(d, 'availableCash')
        || _getVal(d, 'totalavailablemargin')       // Angel RMS
        || _getVal(d, 'availablemargin')
        || _getVal(d, 'withdrawableBalance')        // Dhan
        || _getVal(d, 'net')                        // Angel RMS
        || _getVal(d, 'totalBalance')
        || 0;
}

function _extractMarginUsed(funds) {
    if (!funds || !funds.data) return 0;
    const d = funds.data;
    return _getVal(d, 'utilizedamount')              // Angel (lowercase)
        || _getVal(d, 'utilizedAmount')              // Dhan
        || _getVal(d, 'marginUsed')
        || _getVal(d, 'usedMargin')
        || 0;
}

function _extractSodLimit(funds) {
    if (!funds || !funds.data) return 0;
    return _getVal(funds.data, 'sodLimit') || 0;
}

function numberFmt(n) {
    if (n == null || isNaN(n)) return '0';
    return Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
}

// ================================================================
// REFRESH
// ================================================================
function refreshPortfolio() {
    showToast('🔄', 'Refreshing portfolio...');
    loadPortfolio().then(() => {
        showToast('✅', 'Portfolio refreshed');
    });
}

// ================================================================
// LIVE PRICE SIMULATION (fallback when no broker data)
// ================================================================
function simulatePriceChange() {
    // Not needed with real broker data — prices come live from broker
}

function startSimulation() {
    if (_portfolioSimInterval) return;
    // Refresh portfolio every 15 seconds for live data
    _portfolioSimInterval = setInterval(() => {
        // Only reload if we have real data
        if (portfolioData.funds?.success || portfolioData.positions?.length > 0) {
            loadPortfolio();
        }
    }, 15000);
}

function stopSimulation() {
    if (_portfolioSimInterval) {
        clearInterval(_portfolioSimInterval);
        _portfolioSimInterval = null;
    }
}
