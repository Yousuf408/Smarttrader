// ================================================================
// INTRADAY PORTFOLIO — full-featured dashboard
// ================================================================

const BUDGET = 100000;
const MARGIN_MULTIPLIER = 5;

const holdingsData = [
    { 
        symbol: 'RELIANCE', 
        qty: 10, 
        buyAvg: 2810, 
        sellAvg: null,
        current: 2856, 
        sector: 'Energy', 
        volume: '1.2M',
        entryTime: '09:15 AM',
        marginUsed: 28100,
        budgetUsed: 5620,
        isSold: false
    },
    { 
        symbol: 'TCS', 
        qty: 5, 
        buyAvg: 3890, 
        sellAvg: 3925,
        current: 3920, 
        sector: 'IT', 
        volume: '850K',
        entryTime: '09:30 AM',
        marginUsed: 19450,
        budgetUsed: 3890,
        isSold: true
    },
    { 
        symbol: 'INFY', 
        qty: 15, 
        buyAvg: 1520, 
        sellAvg: null,
        current: 1545, 
        sector: 'IT', 
        volume: '2.1M',
        entryTime: '10:00 AM',
        marginUsed: 22800,
        budgetUsed: 4560,
        isSold: false
    },
    { 
        symbol: 'HDFC', 
        qty: 8, 
        buyAvg: 1700, 
        sellAvg: null,
        current: 1680, 
        sector: 'Banking', 
        volume: '950K',
        entryTime: '10:15 AM',
        marginUsed: 13600,
        budgetUsed: 2720,
        isSold: false
    },
    { 
        symbol: 'TATAMOTORS', 
        qty: 12, 
        buyAvg: 890, 
        sellAvg: null,
        current: 905, 
        sector: 'Auto', 
        volume: '1.8M',
        entryTime: '11:00 AM',
        marginUsed: 10680,
        budgetUsed: 2136,
        isSold: false
    }
];

function loadPortfolio() {
    const holdings = holdingsData;
    
    const activeHoldings = holdings.filter(h => !h.isSold);
    const totalValue = activeHoldings.reduce((s, h) => s + (h.current * h.qty), 0);
    const invested = activeHoldings.reduce((s, h) => s + (h.buyAvg * h.qty), 0);
    const totalPnl = totalValue - invested;
    const totalPnlPercent = invested > 0 ? (totalPnl / invested) * 100 : 0;
    
    const totalMarginUsed = activeHoldings.reduce((s, h) => s + h.marginUsed, 0);
    const totalBudgetUsed = activeHoldings.reduce((s, h) => s + h.budgetUsed, 0);
    const pnlOnBudget = totalBudgetUsed > 0 ? (totalPnl / totalBudgetUsed) * 100 : 0;
    const pnlOnMargin = totalMarginUsed > 0 ? (totalPnl / totalMarginUsed) * 100 : 0;
    
    const gainers = activeHoldings.filter(h => h.current > h.buyAvg).length;
    const losers = activeHoldings.filter(h => h.current < h.buyAvg).length;

    document.getElementById('gainersCount').textContent = `▲ ${gainers}`;
    document.getElementById('losersCount').textContent = `▼ ${losers}`;

    // Render Stats
    document.getElementById('portfolioStats').innerHTML = `
        <div class="stat-box">
            <div class="stat-icon">💰</div>
            <div class="label">Total Value</div>
            <div class="value">₹${totalValue.toLocaleString()}</div>
            <div class="sub ${totalPnl >= 0 ? 'green' : 'red'}">
                ${totalPnl >= 0 ? '↑' : '↓'} ₹${Math.abs(totalPnl).toLocaleString()} 
                <span style="font-weight:400;color:var(--text-muted);">(${totalPnlPercent >= 0 ? '+' : ''}${totalPnlPercent.toFixed(2)}%)</span>
            </div>
        </div>
        <div class="stat-box">
            <div class="stat-icon">📊</div>
            <div class="label">Invested</div>
            <div class="value">₹${invested.toLocaleString()}</div>
            <div class="sub" style="color:var(--text-muted);font-size:9px;">
                <div class="progress-mini">
                    <div class="progress-fill" style="width:${invested > 0 ? Math.round((invested/totalValue)*100) : 0}%;background:var(--gradient-brand);"></div>
                </div>
                ${invested > 0 ? Math.round((invested/totalValue)*100) : 0}% allocated
            </div>
        </div>
        <div class="stat-box">
            <div class="stat-icon">💵</div>
            <div class="label">Available</div>
            <div class="value">₹${Math.round(totalValue * 0.32).toLocaleString()}</div>
            <div class="sub" style="color:var(--text-muted);font-size:9px;">
                <div class="progress-mini">
                    <div class="progress-fill" style="width:32%;background:var(--color-success);"></div>
                </div>
                32% free
            </div>
        </div>
        <div class="stat-box" style="border-left:3px solid ${totalPnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
            <div class="stat-icon">📈</div>
            <div class="label">Day P&L</div>
            <div class="value" style="color:${totalPnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${totalPnl >= 0 ? '+' : ''}₹${totalPnl.toLocaleString()}
            </div>
            <div class="sub ${totalPnl >= 0 ? 'green' : 'red'}">
                ${totalPnl >= 0 ? '↑' : '↓'} ${Math.abs(totalPnlPercent).toFixed(2)}%
                <span style="font-weight:400;color:var(--text-muted);margin-left:4px;font-size:9px;">
                    ${gainers} gainers · ${losers} losers
                </span>
            </div>
        </div>
    `;

    // Render Intraday Metrics
    renderIntradayMetrics(activeHoldings, totalPnl, totalBudgetUsed, totalMarginUsed);

    // Render Holdings Table
    renderHoldingsTable(holdings);

    const now = new Date();
    const lu = document.getElementById('lastUpdated');
    if (lu) lu.textContent = `Last updated: ${now.toLocaleTimeString()}`;
}

// ================================================================
// INTRADAY METRICS
// ================================================================
function renderIntradayMetrics(holdings, totalPnl, totalBudgetUsed, totalMarginUsed) {
    const pnlOnBudget = totalBudgetUsed > 0 ? (totalPnl / totalBudgetUsed) * 100 : 0;
    const pnlOnMargin = totalMarginUsed > 0 ? (totalPnl / totalMarginUsed) * 100 : 0;
    
    let best = holdings[0], worst = holdings[0];
    if (holdings.length > 0) {
        holdings.forEach(h => {
            const pnl = ((h.current - h.buyAvg) / h.buyAvg) * 100;
            const bestPnl = best ? ((best.current - best.buyAvg) / best.buyAvg) * 100 : -Infinity;
            const worstPnl = worst ? ((worst.current - worst.buyAvg) / worst.buyAvg) * 100 : Infinity;
            if (pnl > bestPnl) best = h;
            if (pnl < worstPnl) worst = h;
        });
    }

    const avgReturn = holdings.length > 0 
        ? holdings.reduce((s, h) => s + ((h.current - h.buyAvg) / h.buyAvg * 100), 0) / holdings.length 
        : 0;
    const leverageUsed = totalBudgetUsed > 0 ? (totalMarginUsed / totalBudgetUsed).toFixed(1) : '0.0';
    const winRate = holdings.length > 0 
        ? (holdings.filter(h => h.current > h.buyAvg).length / holdings.length * 100).toFixed(0) 
        : '0';

    const el = document.getElementById('intradayMetrics');
    if (!el) return;

    el.innerHTML = `
        <div class="metric-card" style="border-left:2px solid var(--brand-start);">
            <div class="metric-label">💰 Budget P&L</div>
            <div class="metric-value" style="color:${pnlOnBudget > 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${pnlOnBudget > 0 ? '+' : ''}${pnlOnBudget.toFixed(2)}%
            </div>
            <div class="metric-sub">₹${Math.abs(totalPnl).toLocaleString()}</div>
        </div>
        <div class="metric-card" style="border-left:2px solid var(--color-warning);">
            <div class="metric-label">📈 Margin P&L</div>
            <div class="metric-value" style="color:${pnlOnMargin > 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${pnlOnMargin > 0 ? '+' : ''}${pnlOnMargin.toFixed(2)}%
            </div>
            <div class="metric-sub">${leverageUsed}x leverage</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">🏆 Best</div>
            <div class="metric-value" style="color:var(--color-success);font-size:15px;">
                ${best ? best.symbol : '—'}
            </div>
            <div class="metric-sub">${best ? '+' + ((best.current - best.buyAvg) / best.buyAvg * 100).toFixed(2) + '%' : ''}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">📉 Worst</div>
            <div class="metric-value" style="color:var(--color-danger);font-size:15px;">
                ${worst ? worst.symbol : '—'}
            </div>
            <div class="metric-sub">${worst ? ((worst.current - worst.buyAvg) / worst.buyAvg * 100).toFixed(2) + '%' : ''}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">📊 Win Rate</div>
            <div class="metric-value" style="color:${Number(winRate) >= 50 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${winRate}%
            </div>
            <div class="metric-sub">${holdings.filter(h => h.current > h.buyAvg).length}/${holdings.length}</div>
        </div>
        <div class="metric-card">
            <div class="metric-label">📊 Avg Return</div>
            <div class="metric-value" style="color:${avgReturn > 0 ? 'var(--color-success)' : 'var(--color-danger)'};">
                ${avgReturn > 0 ? '+' : ''}${avgReturn.toFixed(2)}%
            </div>
            <div class="metric-sub">Per position</div>
        </div>
    `;
}

// ================================================================
// HOLDINGS TABLE
// ================================================================
function renderHoldingsTable(holdings) {
    const el = document.getElementById('holdingsTable');
    if (!el) return;

    if (holdings.length === 0) {
        el.innerHTML = '<div style="text-align:center;padding:30px;color:var(--text-muted);font-size:13px;">No positions to display</div>';
        return;
    }

    el.innerHTML = `
        <table class="table-modern">
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Sector</th>
                    <th>Entry</th>
                    <th>Qty</th>
                    <th>Buy Avg</th>
                    <th>Sell Avg</th>
                    <th>LTP</th>
                    <th>P&L</th>
                    <th>P&L %</th>
                    <th>Action</th>
                </tr>
            </thead>
            <tbody>
                ${holdings.map((h) => {
                    let pnl, pnlPercent;
                    if (h.isSold && h.sellAvg) {
                        pnl = (h.sellAvg - h.buyAvg) * h.qty;
                        pnlPercent = ((h.sellAvg - h.buyAvg) / h.buyAvg) * 100;
                    } else {
                        pnl = (h.current - h.buyAvg) * h.qty;
                        pnlPercent = ((h.current - h.buyAvg) / h.buyAvg) * 100;
                    }
                    const isGainer = pnl > 0;
                    const sectorClass = isGainer ? 'positive' : 'negative';
                    const dotColor = isGainer ? 'green' : 'red';
                    
                    const displaySellAvg = h.isSold && h.sellAvg ? 
                        `<span style="color:var(--text-muted);font-size:10px;">₹${h.sellAvg.toLocaleString()}</span>` : 
                        `<span style="color:var(--text-muted);font-size:10px;">—</span>`;

                    return `
                        <tr style="${!isGainer ? 'background:rgba(225,112,85,0.02);' : ''}">
                            <td>
                                <div style="display:flex;align-items:center;gap:4px;">
                                    <span class="status-dot ${dotColor}"></span>
                                    <span class="symbol-highlight">${h.symbol}</span>
                                    ${h.isSold ? '<span class="sold-badge">SOLD</span>' : ''}
                                </div>
                            </td>
                            <td><span class="sector-tag ${sectorClass}">${h.sector}</span></td>
                            <td><span class="entry-badge">${h.entryTime}</span></td>
                            <td>${h.qty}</td>
                            <td style="font-weight:600;font-size:11px;">₹${h.buyAvg.toLocaleString()}</td>
                            <td>${displaySellAvg}</td>
                            <td style="font-weight:600;font-size:11px;">₹${h.current.toLocaleString()}</td>
                            <td style="color:${isGainer ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:700;font-size:11px;">
                                ${isGainer ? '+' : ''}₹${pnl.toLocaleString()}
                            </td>
                            <td>
                                <span style="color:${isGainer ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:700;font-size:11px;">
                                    ${isGainer ? '+' : ''}${pnlPercent.toFixed(2)}%
                                </span>
                            </td>
                            <td>
                                <div class="action-group">
                                    ${!h.isSold ? `
                                        <button class="action-btn action-btn-sell" onclick="showToast('📤 Sold','${h.symbol} sold at ₹${h.current}')">
                                            Sell
                                        </button>
                                        <button class="action-btn action-btn-sl" onclick="showToast('⛔ SL Set','SL for ${h.symbol} at ₹${(h.current * 0.97).toFixed(0)}')">
                                            SL
                                        </button>
                                    ` : `
                                        <span style="font-size:8px;color:var(--text-muted);">Closed</span>
                                    `}
                                    <button class="action-btn action-btn-book" onclick='showToast("📊","${h.symbol} details shown")'>
                                        📊
                                    </button>
                                </div>
                            </td>
                        </tr>
                    `;
                }).join('')}
            </tbody>
        </table>
    `;
    const countEl = document.getElementById('holdingsCount');
    if (countEl) countEl.textContent = `Showing ${holdings.length} positions (${holdings.filter(h => !h.isSold).length} active)`;
}

// ================================================================
// REFRESH
// ================================================================
function refreshPortfolio() {
    showToast('🔄', 'Refreshing portfolio...');
    setTimeout(() => {
        loadPortfolio();
        showToast('✅', 'Portfolio refreshed');
    }, 500);
}

// ================================================================
// LIVE PRICE SIMULATION
// ================================================================
function simulatePriceChange() {
    holdingsData.forEach(h => {
        if (!h.isSold) {
            const change = (Math.random() - 0.48) * 12;
            h.current = Math.max(h.buyAvg - 100, Math.min(h.buyAvg + 150, h.current + change));
            h.current = Math.round(h.current);
        }
    });
    loadPortfolio();
}

// Auto-refresh prices every 5s when portfolio is active
let simInterval = null;
function startSimulation() {
    if (simInterval) return;
    simInterval = setInterval(simulatePriceChange, 5000);
}
function stopSimulation() {
    if (simInterval) {
        clearInterval(simInterval);
        simInterval = null;
    }
}

// Override loadPortfolio to auto-start simulation
const _origLoadPortfolio = loadPortfolio;
loadPortfolio = function() {
    startSimulation();
    _origLoadPortfolio();
};
