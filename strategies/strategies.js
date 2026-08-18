// ================================================================
// STRATEGIES PAGE
// ================================================================
function loadStrategies() {
    const strategies = [
        { id: 1, name: 'Advance ORB', desc: 'Opening Range Breakout Strategy', stocks: 6, winRate: 72, status: 'active' },
        { id: 2, name: 'SmartMoney', desc: 'Breakout + Volume Confirmation Strategy', stocks: 5, winRate: 65, status: 'active' },
        { id: 3, name: 'Big Players', desc: 'Support & Resistance Strategy', stocks: 6, winRate: 58, status: 'paused' }
    ];
    
    document.getElementById('strategyGrid').innerHTML = strategies.map(s => `
        <div class="strategy-card">
            <div class="name">${s.id === 1 ? '📈' : s.id === 2 ? '💰' : '🏢'} ${s.name}</div>
            <div class="desc">${s.desc}</div>
            <div class="meta">
                <span>Stocks: <span class="highlight">${s.stocks}</span></span>
                <span>Win Rate: <span style="color:${s.winRate >= 65 ? 'var(--color-success)' : 'var(--color-warning)'};">${s.winRate}%</span></span>
                <span class="status-badge"><span class="dot-small ${s.status === 'active' ? 'green' : 'yellow'}"></span>${s.status === 'active' ? 'Active' : 'Paused'}</span>
            </div>
            <div style="display:flex;gap:8px;flex-wrap:wrap;">
                <button class="btn btn-success btn-sm" onclick="showToast('▶ Started','${s.name} active')">▶ Start</button>
                <button class="btn btn-outline btn-sm" onclick="openModal('edit')">✏️ Edit</button>
                <button class="btn btn-danger btn-sm" onclick="showToast('⏹ Stopped','${s.name} paused')">⏹ Stop</button>
            </div>
        </div>
    `).join('');

    // Performance Summary
    const perf = [
        { name: 'Advance ORB', trades: 45, winRate: 72, pnl: '+₹12,400', avg: '+2.8%' },
        { name: 'SmartMoney', trades: 28, winRate: 65, pnl: '+₹6,800', avg: '+1.9%' },
        { name: 'Big Players', trades: 18, winRate: 58, pnl: '-₹2,100', avg: '-0.8%' }
    ];
    document.getElementById('strategyPerformance').innerHTML = `
        <table class="table-modern">
            <thead><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Total P&L</th><th>Avg Return</th></tr></thead>
            <tbody>
                ${perf.map(p => `
                    <tr>
                        <td><strong>${p.name}</strong></td>
                        <td>${p.trades}</td>
                        <td style="color:${p.winRate >= 65 ? 'var(--color-success)' : 'var(--color-warning)'};font-weight:600;">${p.winRate}%</td>
                        <td style="color:${p.pnl.includes('+') ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:600;">${p.pnl}</td>
                        <td style="color:${p.avg.includes('+') ? 'var(--color-success)' : 'var(--color-danger)'};font-weight:600;">${p.avg}</td>
                    </tr>
                `).join('')}
            </tbody>
        </table>
    `;
}

// ================================================================
// SAVE STRATEGY (Modal)
// ================================================================
function saveStrategy() {
    const name = DOM.modalStrategyName.value || 'Unnamed Strategy';
    showToast('💾 Strategy Saved', `"${name}" saved successfully`);
    closeModal();
    loadStrategies();
}
