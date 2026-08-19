// ================================================================
// HOME PAGE
// ================================================================
function loadHome() {
    document.getElementById('s1').textContent = Math.floor(40 + Math.random() * 20);
    document.getElementById('s2').textContent = Math.floor(15 + Math.random() * 10);
    document.getElementById('s3').textContent = Math.floor(60 + Math.random() * 15) + '%';
    document.getElementById('s4').textContent = `+₹${Math.floor(3000 + Math.random() * 5000)}`;

    // Recent Trades
    const trades = [
        { symbol: 'RELIANCE', type: 'BUY', qty: 10, price: '₹2,856', pnl: '+₹1,240' },
        { symbol: 'TCS', type: 'SELL', qty: 5, price: '₹3,920', pnl: '-₹320' },
        { symbol: 'INFY', type: 'BUY', qty: 15, price: '₹1,545', pnl: '+₹2,100' }
    ];
    document.getElementById('recentTrades').innerHTML = `
        <table class="table-modern">
            <thead><tr><th>Symbol</th><th>Type</th><th>Qty</th><th>Price</th><th>P&L</th></tr></thead>
            <tbody>
                ${trades.map(t => `
                    <tr data-symbol="${t.symbol}">
                        <td><strong>${t.symbol}</strong></td>
                        <td style="color:${t.type === 'BUY' ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:600;">${t.type}</td>
                        <td>${t.qty}</td>
                        <td class="trade-price">${t.price}</td>
                        <td style="color:${t.pnl.includes('+') ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:600;">${t.pnl}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;

    // Market Overview (showing SENSEX and NIFTY)
    const markets = [
        { name: 'SENSEX', value: '+0.6%', up: true }
    ];
    document.getElementById('marketOverview').innerHTML = markets.map(m => `
        <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.04);">
            <span style="font-weight:500;">${m.name}</span>
            <span style="color:${m.up ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:700;">${m.value}</span>
        </div>
    `).join('');
}

// Live tick hook for home dashboard
if (typeof window !== 'undefined') {
    const updateHomeTicks = (ticks) => {
        if (!ticks) return;
        const rows = document.querySelectorAll('#recentTrades tr[data-symbol]');
        for (const r of rows) {
            const sym = r.getAttribute('data-symbol');
            const tick = ticks[sym] || ticks[`${sym}-EQ`];
            if (tick && tick.ltp) {
                const cell = r.querySelector('.trade-price');
                if (cell) {
                    cell.textContent = `₹${Number(tick.ltp).toFixed(2)}`;
                }
            }
        }
    };
    if (window.LiveFeedManager) {
        window.LiveFeedManager.subscribe(updateHomeTicks);
    }
}

