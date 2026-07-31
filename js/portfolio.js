// ================================================================
// PORTFOLIO PAGE — Real data from broker API
// ================================================================

function loadPortfolio() {
    const statsEl = document.getElementById('portfolioStats');
    const tableEl = document.getElementById('holdingsTable');

    statsEl.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;"><div class="spinner"></div><p style="margin-top:12px;color:var(--text-muted);">Loading portfolio data...</p></div>';
    tableEl.innerHTML = '';

    fetch('/api/portfolio/holdings')
        .then(r => r.json())
        .then(data => {
            const holdings = data.holdings || [];
            const summary = data.summary || {};

            // Portfolio Summary Cards
            const totalPnl = summary.total_pnl || 0;
            statsEl.innerHTML = `
                <div class="stat-box">
                    <div class="label">💰 Total Value</div>
                    <div class="value">₹${(summary.total_value || 0).toLocaleString('en-IN')}</div>
                    <div class="sub ${totalPnl >= 0 ? 'green' : 'red'}">${totalPnl >= 0 ? '↑' : '↓'} ₹${Math.abs(totalPnl).toLocaleString('en-IN')}</div>
                </div>
                <div class="stat-box">
                    <div class="label">📊 Invested</div>
                    <div class="value">₹${(summary.invested || 0).toLocaleString('en-IN')}</div>
                    <div class="sub" style="color:var(--text-muted);">${summary.invested > 0 ? Math.round((summary.invested / (summary.total_value || 1)) * 100) : 0}% allocated</div>
                </div>
                <div class="stat-box">
                    <div class="label">💵 Available Cash</div>
                    <div class="value">₹${(summary.cash || 0).toLocaleString('en-IN')}</div>
                    <div class="sub" style="color:var(--text-muted);">${summary.cash > 0 ? Math.round((summary.cash / ((summary.total_value || 0) + (summary.cash || 0))) * 100) : 0}% free</div>
                </div>
                <div class="stat-box">
                    <div class="label">📈 Total P&L</div>
                    <div class="value" style="color:${totalPnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)'};">${totalPnl >= 0 ? '+' : ''}₹${Math.abs(totalPnl).toLocaleString('en-IN')}</div>
                    <div class="sub ${totalPnl >= 0 ? 'green' : 'red'}">${totalPnl >= 0 ? '↑' : '↓'} ${Math.abs(summary.total_pnl_pct || 0)}%</div>
                </div>
            `;

            // Holdings Table
            if (holdings.length === 0) {
                tableEl.innerHTML = '<div style="text-align:center;padding:40px;color:var(--text-muted);font-size:14px;">No holdings found. Connect a broker to see your real positions.</div>';
                return;
            }

            tableEl.innerHTML = `
                <table class="table-modern" id="portfolioHoldingsTable">
                    <thead>
                        <tr>
                            <th>Symbol</th>
                            <th>Qty</th>
                            <th>Avg Price</th>
                            <th>Current</th>
                            <th>P&L</th>
                            <th>P&L %</th>
                            <th>Action</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${holdings.map(h => {
                            const pnl = h.pnl || 0;
                            const pnlPct = h.pnl_pct || 0;
                            const pnlClass = pnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                            return `
                                <tr>
                                    <td><strong>${h.symbol}</strong></td>
                                    <td>${h.qty}</td>
                                    <td>₹${(h.avg_price || 0).toFixed(2)}</td>
                                    <td>₹${(h.current || 0).toFixed(2)}</td>
                                    <td style="color:${pnlClass};font-weight:600;">${pnl >= 0 ? '+' : ''}₹${Math.abs(pnl).toFixed(2)}</td>
                                    <td style="color:${pnlClass};font-weight:600;">${pnl >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%</td>
                                    <td><button class="btn btn-outline btn-sm" onclick="showToast('📤 ${h.symbol}','Sell order window coming soon')">Sell</button></td>
                                </tr>
                            `;
                        }).join('')}
                    </tbody>
                </table>
            `;
        })
        .catch(err => {
            statsEl.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:40px;color:var(--color-danger);font-size:14px;">⚠️ Failed to load portfolio: ' + err.message + '</div>';
        });
}
