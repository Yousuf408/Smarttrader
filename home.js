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
                    <tr>
                        <td><strong>${t.symbol}</strong></td>
                        <td style="color:${t.type === 'BUY' ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:600;">${t.type}</td>
                        <td>${t.qty}</td>
                        <td>${t.price}</td>
                        <td style="color:${t.pnl.includes('+') ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:600;">${t.pnl}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;

    // Market Overview
    const markets = [
        { name: 'NIFTY 50', value: '+0.8%', up: true },
        { name: 'SENSEX', value: '+0.6%', up: true },
        { name: 'BANK NIFTY', value: '-0.3%', up: false },
        { name: 'VIX', value: '+2.1%', up: true }
    ];
    document.getElementById('marketOverview').innerHTML = markets.map(m => `
        <div style="display:flex;justify-content:space-between;padding:8px 0;border-bottom:1px solid rgba(0,0,0,0.04);">
            <span style="font-weight:500;">${m.name}</span>
            <span style="color:${m.up ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:700;">${m.value}</span>
        </div>
    `).join('');
}
