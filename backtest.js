// ================================================================
// BACKTEST PAGE
// ================================================================
function runBacktest() {
    const btn = event?.target;
    const originalText = btn.textContent;
    const container = document.getElementById('backtestResults');
    
    btn.innerHTML = '<span class="spinner"></span> Running...';
    btn.disabled = true;
    
    container.innerHTML = `<div class="chart-placeholder"><div><div class="big-icon">⏳</div><p style="font-weight:600;">Running backtest...</p><div style="margin-top:12px;"><span class="spinner"></span></div></div></div>`;
    
    setTimeout(() => {
        // Backtest Results Container
        container.innerHTML = `
            <div class="chart-placeholder" style="border-color:var(--color-success);border-style:solid;">
                <div>
                    <div class="big-icon">📈</div>
                    <p style="color:var(--color-success);font-weight:700;font-size:18px;">Backtest Complete!</p>
                    <p style="font-size:14px;color:var(--text-secondary);margin-top:4px;">Total Return: <span style="color:var(--color-success);font-weight:700;">+18.4%</span> · Sharpe Ratio: <span style="font-weight:700;">1.42</span></p>
                    <p style="font-size:14px;color:var(--text-secondary);">Max Drawdown: <span style="color:var(--color-danger);font-weight:700;">-6.2%</span> · Win Rate: <span style="font-weight:700;">68%</span></p>
                </div>
            </div>
        `;
        
        // Backtest Metrics Table
        document.getElementById('backtestMetrics').innerHTML = `
            <table class="table-modern">
                <thead><tr><th>Metric</th><th>Value</th></tr></thead>
                <tbody>
                    <tr><td>Total Return</td><td style="color:var(--color-success);font-weight:700;">+18.4%</td></tr>
                    <tr><td>Sharpe Ratio</td><td style="font-weight:700;">1.42</td></tr>
                    <tr><td>Max Drawdown</td><td style="color:var(--color-danger);font-weight:700;">-6.2%</td></tr>
                    <tr><td>Win Rate</td><td style="color:var(--color-success);font-weight:700;">68%</td></tr>
                    <tr><td>Number of Trades</td><td style="font-weight:700;">142</td></tr>
                </tbody>
            </table>
        `;
        
        showToast('📊 Backtest Complete', 'Results updated successfully');
        btn.innerHTML = originalText;
        btn.disabled = false;
    }, 1500);
}
