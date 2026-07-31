// ================================================================
// HOME PAGE — Real data from backend + live ticks
// ================================================================

function loadHome() {
    // Fetch live broker status + portfolio summary
    fetch('/api/broker/status')
        .then(r => r.json())
        .then(status => {
            const connected = status.connected || false;
            const broker = status.broker || '';
            // Update Live indicator
            const dot = document.querySelector('.status .dot');
            const label = document.querySelector('.status');
            if (dot) {
                dot.style.background = connected ? 'var(--color-success)' : '#aaa';
                dot.style.animation = connected ? 'pulse 1.5s infinite' : 'none';
            }
            if (label) {
                const textNode = label.childNodes[2];
                if (textNode) textNode.textContent = connected ? ` Live (${broker})` : ' Offline';
            }
        })
        .catch(() => {});

    // Fetch portfolio data for stats
    fetch('/api/portfolio/holdings')
        .then(r => r.json())
        .then(data => {
            const summary = data.summary || {};
            // Shortlisted: count of stocks in watchlist
            const s1El = document.getElementById('s1');
            if (s1El) s1El.textContent = data.holdings?.length || '0';

            // Active trades: from holdings
            const s2El = document.getElementById('s2');
            if (s2El) s2El.textContent = data.holdings?.length || '0';

            // Win rate from P&L
            const holdings = data.holdings || [];
            const winners = holdings.filter(h => (h.pnl || 0) > 0);
            const winRate = holdings.length > 0 ? Math.round((winners.length / holdings.length) * 100) : 0;
            const s3El = document.getElementById('s3');
            if (s3El) s3El.textContent = winRate + '%';
            const s3Sub = s3El?.parentElement?.querySelector('.sub');
            if (s3Sub) {
                s3Sub.textContent = winRate >= 50 ? `↑ ${winners.length}/${holdings.length} profitable` : `↓ ${winners.length}/${holdings.length} profitable`;
                s3Sub.className = `sub ${winRate >= 50 ? 'green' : 'red'}`;
            }

            // P&L Today
            const totalPnl = summary.total_pnl || 0;
            const s4El = document.getElementById('s4');
            if (s4El) {
                s4El.textContent = `${totalPnl >= 0 ? '+' : ''}₹${Math.abs(totalPnl).toLocaleString('en-IN')}`;
                s4El.style.color = totalPnl >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
            }
            const s4Sub = s4El?.parentElement?.querySelector('.sub');
            if (s4Sub && summary.total_pnl_pct) {
                s4Sub.textContent = `${totalPnl >= 0 ? '↑' : '↓'} ${Math.abs(summary.total_pnl_pct)}%`;
                s4Sub.className = `sub ${totalPnl >= 0 ? 'green' : 'red'}`;
            }
        })
        .catch(() => {
            // Fallback if API not available
            document.getElementById('s1').textContent = '—';
            document.getElementById('s2').textContent = '—';
            document.getElementById('s3').textContent = '—%';
            document.getElementById('s4').textContent = '—';
        });

    // Recent Trades — from live ticks (top movers)
    fetch('/api/market/live-ticks')
        .then(r => r.json())
        .then(data => {
            const ticks = data.ticks || {};
            const symbols = Object.keys(ticks);
            const sorted = symbols
                .filter(s => !s.includes('-'))
                .sort((a, b) => Math.abs(ticks[b]?.change_pct || 0) - Math.abs(ticks[a]?.change_pct || 0))
                .slice(0, 5);

            const el = document.getElementById('recentTrades');
            if (!el) return;
            if (sorted.length === 0) {
                el.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:13px;">No tick data — connect a broker to see live prices</div>';
                return;
            }
            el.innerHTML = `
                <table class="table-modern">
                    <thead><tr><th>Symbol</th><th>LTP</th><th>Change</th><th>Volume</th></tr></thead>
                    <tbody>
                        ${sorted.map(s => {
                            const t = ticks[s];
                            const ltp = t?.ltp || 0;
                            const chg = t?.change_pct || 0;
                            const vol = t?.volume || 0;
                            const arrow = chg >= 0 ? '▲' : '▼';
                            const color = chg >= 0 ? 'var(--color-success)' : 'var(--color-danger)';
                            let volStr;
                            if (vol >= 1_000_000) volStr = (vol/1_000_000).toFixed(1) + 'M';
                            else if (vol >= 1_000) volStr = (vol/1_000).toFixed(1) + 'K';
                            else volStr = vol.toString();
                            return `<tr>
                                <td><strong>${s}</strong></td>
                                <td>₹${ltp.toFixed(2)}</td>
                                <td style="color:${color};font-weight:600;">${arrow} ${Math.abs(chg).toFixed(2)}%</td>
                                <td>${volStr}</td>
                            </tr>`;
                        }).join('')}
                    </tbody>
                </table>
            `;
        })
        .catch(() => {
            const el = document.getElementById('recentTrades');
            if (el) el.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text-muted);font-size:13px;">Could not load recent trades</div>';
        });

    // Market Overview — from indices API
    fetch('/api/market/indices')
        .then(r => r.json())
        .then(data => {
            const indices = data.data || [];
            const find = (name) => indices.find(i => i.index === name);
            const items = [
                { name: 'NIFTY 50', data: find('NIFTY 50') },
                { name: 'BANK NIFTY', data: find('NIFTY BANK') },
                { name: 'INDIA VIX', data: find('INDIA VIX') },
                { name: 'NIFTY 500', data: find('NIFTY 500') },
            ];
            const el = document.getElementById('marketOverview');
            if (!el) return;
            el.innerHTML = items.map(item => {
                const d = item.data;
                if (!d) return '';
                const last = d.last || 0;
                const chg = d.change;
                const pct = d.pChange;
                const isUp = chg != null && chg >= 0;
                const arrow = chg != null ? (isUp ? '▲' : '▼') : '—';
                const pctStr = (pct != null && !isNaN(pct)) ? `${arrow} ${Math.abs(pct).toFixed(2)}%` : '';
                const color = chg != null ? (isUp ? 'var(--color-success)' : 'var(--color-danger)') : 'var(--text-muted)';
                return `<div style="display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid rgba(0,0,0,0.04);">
                    <span style="font-weight:500;">${item.name}</span>
                    <span style="color:${color};font-weight:700;">${last.toLocaleString('en-IN')} ${pctStr}</span>
                </div>`;
            }).filter(Boolean).join('');
        })
        .catch(() => {});
}
