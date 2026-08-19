// ================================================================
// GLOBAL STATE & DOM HELPERS
// ================================================================
const DOM = {
    get navLinks() { return document.querySelectorAll('.sidebar-link'); },
    get toast() { return document.getElementById('toast'); },
    get toastTitle() { return document.getElementById('toastTitle'); },
    get toastMessage() { return document.getElementById('toastMessage'); }
};

let autoBuyEnabled = false;

// ================================================================
// CENTRAL WEBSOCKET / SSE LIVE FEED MANAGER
// ================================================================
window.LiveFeedManager = {
    _eventSource: null,
    _listeners: new Set(),
    _lastTicks: {},

    subscribe(fn) {
        if (typeof fn === 'function') {
            this._listeners.add(fn);
            if (Object.keys(this._lastTicks).length > 0) {
                try { fn(this._lastTicks); } catch (_) {}
            }
        }
        return () => this._listeners.delete(fn);
    },

    start() {
        if (this._eventSource) return;
        this._eventSource = new EventSource('/api/market/live-ticks/stream');
        this._eventSource.onmessage = (ev) => {
            try {
                const payload = JSON.parse(ev.data);
                if (payload && payload.ticks) {
                    this._lastTicks = payload.ticks;
                    for (const fn of this._listeners) {
                        try { fn(payload.ticks); } catch (_) {}
                    }
                }
            } catch (_) {}
        };
        this._eventSource.onerror = () => {
            // EventSource auto-reconnects natively
        };
    },

    stop() {
        if (this._eventSource) {
            this._eventSource.close();
            this._eventSource = null;
        }
    }
};

// ================================================================
// SIDEBAR TOGGLE
// ================================================================
document.addEventListener('DOMContentLoaded', async () => {
    // ── Start Central Live Ticker Feed ──────────────────────────
    window.LiveFeedManager.start();

    // ── Auth check ────────────────────────────────────────────

    const user = await checkAuth();
    if (user) {
        const name = user.email?.split('@')[0] || 'User';
        const initial = (user.email || 'U')[0].toUpperCase();
        // Sidebar
        const sAv = document.getElementById('sidebarAvatar');
        const sNm = document.getElementById('sidebarUserName');
        if (sAv) sAv.textContent = initial;
        if (sNm) sNm.textContent = `Welcome, ${name}`;
        // Top bar
        const hAv = document.getElementById('headerAvatar');
        if (hAv) hAv.textContent = initial;
        // Dropdown user info
        const dName = document.getElementById('dropdownUserName');
        const dEmail = document.getElementById('dropdownUserEmail');
        if (dName) dName.textContent = name;
        if (dEmail) dEmail.textContent = user.email || '';
    }

    // ── Fetch real capital from broker ───────────────────────────
    try {
        const resp = await fetch('/api/portfolio/funds');
        const data = await resp.json();
        if (data.success && data.data) {
            // Dhan fields: availabelBalance, sodLimit
            // Angel RMS fields: totalavailablemargin, availablemargin, net, availablecash
            const balance = data.data.availabelBalance
                || data.data.sodLimit
                || data.data.totalavailablemargin
                || data.data.availablemargin
                || data.data.availablecash
                || data.data.net
                || 0;
            const capEl = document.getElementById('headerCapital');
            if (capEl) capEl.textContent = '₹' + Math.round(balance).toLocaleString('en-IN');
        }
    } catch (e) {
        // Silently ignore — broker not connected yet
    }

    // ── Initial market status ────────────────────────────────────
    updateMarketStatus();

    // ── Avatar dropdown toggle ───────────────────────────────────
    const avatarWrap = document.getElementById('avatarWrap');
    const dropdown = document.getElementById('avatarDropdown');
    if (avatarWrap && dropdown) {
        avatarWrap.addEventListener('click', (e) => {
            e.stopPropagation();
            dropdown.classList.toggle('open');
        });
        document.addEventListener('click', () => {
            dropdown.classList.remove('open');
        });
    }

    // Sidebar collapse/expand
    const sidebar = document.getElementById('sidebar');
    const toggleBtn = document.getElementById('sidebarToggle');
    if (sidebar && toggleBtn) {
        // Restore saved state
        const saved = localStorage.getItem('sidebarCollapsed');
        if (saved === 'true') sidebar.classList.add('collapsed');
        toggleBtn.addEventListener('click', () => {
            sidebar.classList.toggle('collapsed');
            localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
        });
    }
    // Init theme from localStorage
    applyTheme(localStorage.getItem('tradetheme') || 'light');
    // Start notification polling
    pollNotifications();
    setInterval(pollNotifications, 8000);
});

// ================================================================
// DARK MODE TOGGLE
// ================================================================
function toggleTheme() {
    const current = localStorage.getItem('tradetheme') || 'light';
    const next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    localStorage.setItem('tradetheme', next);
}
function applyTheme(theme) {
    const btn = document.getElementById('themeToggle');
    if (theme === 'dark') {
        document.body.classList.add('dark-theme');
        if (btn) btn.textContent = '☀️';
    } else {
        document.body.classList.remove('dark-theme');
        if (btn) btn.textContent = '🌙';
    }
}

// ================================================================
// KEYBOARD SHORTCUTS — Alt + Key
// ================================================================
document.addEventListener('keydown', (e) => {
    if (e.altKey) {
        const map = {
            'h': 'home',
            's': 'screener',
            'p': 'portfolio',
            't': 'testing',
            'c': 'settings',
        };
        const key = e.key.toLowerCase();
        if (map[key]) {
            e.preventDefault();
            navigateTo(map[key]);
        }
        // Alt+R = Refresh
        if (key === 'r') {
            e.preventDefault();
            const active = document.querySelector('.page.active');
            if (active) {
                const id = active.id.replace('page-', '');
                if (id === 'testing') loadTestingData();
                else if (id === 'screener') refreshScreener();
                else if (id === 'portfolio') loadPortfolio();
                else if (id === 'home') loadHome();
            }
        }
    }
});

// ================================================================
// NOTIFICATION SYSTEM — strategy match alerts
// ================================================================
let notificationHistory = [];
let notifPollCount = 0;

function pollNotifications() {
    notifPollCount++;
    // Check if the screener has any stocks with breakout signals
    const screenerRows = document.querySelectorAll('#screenerBody tr');
    if (screenerRows.length > 0) {
        const breakoutStocks = [];
        screenerRows.forEach((row, idx) => {
            const cells = row.querySelectorAll('td');
            if (cells.length >= 2) {
                const symbol = cells[0]?.textContent?.trim() || '';
                const price = cells[1]?.textContent?.trim() || '';
                const change = cells[2]?.textContent?.trim() || '';
                // Check for breakout indicators (positive change, volume)
                if (change && !change.startsWith('▼') && cells[3]?.textContent?.includes('Yes') || cells[3]?.textContent?.includes('✅')) {
                    breakoutStocks.push({ symbol, price, change });
                }
            }
        });

        // Check active strategy
        const stratSelect = document.getElementById('strategySelect');
        const stratName = stratSelect ? stratSelect.options[stratSelect.selectedIndex]?.text || 'Advance ORB' : 'Advance ORB';

        // Generate notifications for breakout stocks
        breakoutStocks.forEach(stock => {
            const key = `${stratName}-${stock.symbol}`;
            if (!notificationHistory.includes(key)) {
                notificationHistory.push(key);
                addNotification(stratName, stock.symbol, stock.price, stock.change);
            }
        });
    }

    // Also check Big Players strategy table
    const bpRows = document.querySelectorAll('#bpTableBody tr');
    if (bpRows.length > 0) {
        bpRows.forEach(row => {
            const cells = row.querySelectorAll('td');
            if (cells.length >= 3) {
                const symbol = cells[0]?.textContent?.trim() || '';
                const price = cells[1]?.textContent?.trim() || '';
                const change = cells[2]?.textContent?.trim() || '';
                const key = `Big Players-${symbol}`;
                if (!notificationHistory.includes(key)) {
                    notificationHistory.push(key);
                    addNotification('Big Players', symbol, price, change);
                }
            }
        });
    }

    updateNotifBadge();
}

function addNotification(strategy, symbol, price, change) {
    const list = document.getElementById('notifList');
    if (!list) return;
    // Remove empty state
    const empty = list.querySelector('.notif-empty');
    if (empty) empty.remove();

    const div = document.createElement('div');
    div.className = 'notif-item';
    div.innerHTML = `
        <div><span class="notif-strategy">${strategy}</span> · <span class="notif-stock">${symbol}</span></div>
        <div class="notif-meta">Price: ${price} · Change: ${change}</div>
    `;
    list.prepend(div);

    // Limit to 20 items
    while (list.children.length > 20) {
        list.lastChild.remove();
    }
}

function toggleNotifPanel() {
    const panel = document.getElementById('notifPanel');
    if (panel) panel.classList.toggle('open');
}

function updateNotifBadge() {
    const badge = document.getElementById('notifBadge');
    if (!badge) return;
    const list = document.getElementById('notifList');
    const count = list ? list.querySelectorAll('.notif-item:not(.notif-empty)').length : 0;
    if (count > 0) {
        badge.style.display = 'flex';
        badge.textContent = count > 99 ? '99+' : count;
    } else {
        badge.style.display = 'none';
    }
}

// Close notification panel on outside click
document.addEventListener('click', (e) => {
    const panel = document.getElementById('notifPanel');
    const bell = document.getElementById('notifBell');
    if (panel && panel.classList.contains('open')) {
        if (!panel.contains(e.target) && !bell?.contains(e.target)) {
            panel.classList.remove('open');
        }
    }
});

// ================================================================
// STRATEGY CONFIGURATIONS
// ================================================================
const STRATEGIES = {
    advanceorb: {
        id: 'advanceorb',
        name: 'Advance ORB',
        icon: '📈',
        entryRule: 'Opening Range Breakout',
        risk: '2%',
        columns: ['Symbol', 'Price', 'CHG%', 'GAP%', 'Volume', 'RELVOL', 'Inside', 'Breakout', '200 EMA', '9:15 HIGH', 'PREV HIGH', 'MaxQty', 'Sector']
        // ✅ data array removed - will come from backend API
    },
    smartmoney: {
        id: 'smartmoney',
        name: 'SmartMoney',
        icon: '💰',
        entryRule: 'Breakout + Volume Confirmation',
        risk: '2.5%',
        columns: ['Symbol', 'Max Qty', 'Price / Chg%', 'Volume / Rel Vol', 'Signal Time', 'POC / Gap', 'Signal Price / % Chg', 'Prev High', 'Candle Status'],
        data: [
            { symbol: 'CYIENTDLM', maxQty: '179', price: '698.15', change: '+12.06%', volume: '12.9M', relvol: 'N/A', signalTime: 'N/A', poc: 'N/A', gap: 'N/A', signalPrice: 'N/A', prevHigh: '9:45', candleStatus: '9:40 9:45 9:50' },
            { symbol: 'LOTUSDEV', maxQty: '768', price: '162.70', change: '+9.81%', volume: '18.4M', relvol: 'N/A', signalTime: 'N/A', poc: 'N/A', gap: 'N/A', signalPrice: 'N/A', prevHigh: '9:45', candleStatus: '9:40 9:45 9:50' },
            { symbol: 'BLUESTONE', maxQty: '161', price: '776.05', change: '+6.51%', volume: '29.5M', relvol: 'N/A', signalTime: 'N/A', poc: 'N/A', gap: 'N/A', signalPrice: 'N/A', prevHigh: '9:45', candleStatus: '9:40 9:45 9:50' },
            { symbol: 'PNGJLM', maxQty: '196', price: '636.60', change: '+5.97%', volume: '2.5M', relvol: 'N/A', signalTime: 'N/A', poc: 'N/A', gap: 'N/A', signalPrice: 'N/A', prevHigh: '9:45', candleStatus: '9:40 9:45 9:50' },
            { symbol: 'BAJAJ_AUTO', maxQty: '', price: '10998.50', change: '+5.72%', volume: '1.4M', relvol: 'N/A', signalTime: 'N/A', poc: 'N/A', gap: 'N/A', signalPrice: 'N/A', prevHigh: '9:45', candleStatus: '9:40 9:45 9:50' }
        ]
    },
    bigplayers: {
        id: 'bigplayers',
        name: 'Big Players',
        icon: '🏢',
        entryRule: 'Support & Resistance',
        risk: '1.8%',
        columns: ['Symbol', 'Price', 'CHG%', 'Breakout', 'Support Price', '9:15 High', '9:15 Low', 'MaxQty'],
        
    }
};

// ================================================================
// MARKET STATUS — dynamic open/close based on IST
// ================================================================
function updateMarketStatus() {
    const el = document.getElementById('marketStatus');
    if (!el) return;
    const now = new Date();
    // Convert to IST (UTC+5:30)
    const istOffset = 5.5 * 60 * 60 * 1000;
    const ist = new Date(now.getTime() + istOffset * 1);
    const h = ist.getUTCHours();
    const m = ist.getUTCMinutes();
    const mins = h * 60 + m;
    const day = ist.getUTCDay(); // 0=Sun, 6=Sat
    const isOpen = day >= 1 && day <= 5 && mins >= (9 * 60 + 15) && mins < (15 * 60 + 45);
    el.innerHTML = isOpen
        ? '<span class="status-dot green"></span> Market Open'
        : '<span class="status-dot red"></span> Market Closed';
}

// Refresh market status every 30 seconds
setInterval(updateMarketStatus, 30000);

// ================================================================
// LIVE NIFTY / BANK NIFTY (Angel One) — header index chips
// ================================================================
function renderIndexChips(indices) {
    const names = { 'NIFTY': 'IN', 'BANKNIFTY': 'BN' };
    (indices || []).forEach(ix => {
        const name = ix.name;
        if (name !== 'NIFTY' && name !== 'BANKNIFTY') return;
        const valEl = document.getElementById('idxVal' + name);
        const chgEl = document.getElementById('idxChg' + name);
        const chip  = document.getElementById('idxChip' + name);
        if (!valEl || !chgEl || !chip) return;
        const ltp = parseFloat(ix.ltp);
        if (Number.isFinite(ltp)) valEl.textContent = ltp.toLocaleString('en-IN', { maximumFractionDigits: 1 });
        const pct = parseFloat(ix.change_pct);
        if (Number.isFinite(pct)) {
            const sign = pct > 0 ? '+' : '';
            chgEl.textContent = `${sign}${pct.toFixed(2)}%`;
            chgEl.classList.toggle('pos', pct >= 0);
            chgEl.classList.toggle('neg', pct < 0);
        }
        chip.style.opacity = 1;
    });
}

function updateIndices() {
    fetch('/api/market/indices')
        .then(r => r.json())
        .then(d => renderIndexChips(d.indices))
        .catch(() => {});
}
// Don't hit the broker endpoints before auth — poll once when the dashboard
// is visible, then refresh on a modest cadence (index tape doesn't need to be
// sub-second in the header).
document.addEventListener('DOMContentLoaded', function () {
    setTimeout(updateIndices, 1500);
    setInterval(updateIndices, 5000);
});

// ================================================================
// DYNAMIC MODULAR PAGE LOADER & NAVIGATION
// ================================================================
const pageCache = new Map();
let currentPageId = null;

const PAGE_CONFIG = {
    home: {
        url: '/home/home.html',
        init: () => {
            if (typeof loadHome === 'function') loadHome();
        }
    },
    screener: {
        url: '/screener/screener.html',
        init: () => {
            if (typeof initScreener === 'function') initScreener();
            if (typeof onStrategyChange === 'function') onStrategyChange();
        }
    },
    niftyohlc: {
        url: '/nifty_ohlc/nifty_ohlc_page.html',
        init: () => {}
    },
    portfolio: {
        url: '/portfolio/portfolio.html',
        init: () => {
            if (typeof loadPortfolio === 'function') loadPortfolio();
            if (typeof refreshPortfolio === 'function') refreshPortfolio();
        }
    },
    testing: {
        url: '/testing/testing.html',
        init: () => {
            if (typeof loadTesting === 'function') loadTesting();
        }
    },
    settings: {
        url: '/settings/settings.html',
        init: () => {
            if (typeof toggleBrokerFields === 'function') toggleBrokerFields();
            if (typeof updateBrokerStatusBadge === 'function') updateBrokerStatusBadge();
            if (typeof updateCacheStatus === 'function') updateCacheStatus();
        }
    }
};

async function loadPageHtml(pageId) {
    if (pageCache.has(pageId)) {
        return pageCache.get(pageId);
    }
    const config = PAGE_CONFIG[pageId];
    if (!config) return null;
    try {
        const resp = await fetch(config.url);
        if (!resp.ok) throw new Error(`HTTP ${resp.status} fetching ${config.url}`);
        const html = await resp.text();
        pageCache.set(pageId, html);
        return html;
    } catch (err) {
        console.error(`Failed to load page ${pageId}:`, err);
        return `<div class="panel-glass" style="padding:24px;"><h3>⚠️ Failed to load page</h3><p style="color:var(--text-muted);">${err.message}</p></div>`;
    }
}

async function navigateTo(pageId) {
    const config = PAGE_CONFIG[pageId];
    if (!config) return;

    // Update active sidebar link
    document.querySelectorAll('.sidebar-link').forEach(a => a.classList.remove('active'));
    const activeLink = document.querySelector(`.sidebar-link[data-page="${pageId}"]`);
    if (activeLink) activeLink.classList.add('active');

    // Stop portfolio simulation when navigating away
    if (currentPageId === 'portfolio' && pageId !== 'portfolio' && typeof stopSimulation === 'function') {
        stopSimulation();
    }
    // Stop testing auto-refresh when navigating away
    if (currentPageId === 'testing' && pageId !== 'testing' && typeof stopTestingAutoRefresh === 'function') {
        stopTestingAutoRefresh();
    }

    currentPageId = pageId;

    const mainContainer = document.getElementById('mainContainer');
    if (!mainContainer) return;

    const html = await loadPageHtml(pageId);
    mainContainer.innerHTML = html;

    // Execute page-specific init callback
    try {
        if (typeof config.init === 'function') {
            config.init();
        }
    } catch (e) {
        console.error(`Error initializing page ${pageId}:`, e);
    }

    // Refresh market status on every navigation
    updateMarketStatus();
}

document.querySelectorAll('.sidebar-link').forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();
        const pageId = this.getAttribute('data-page');
        if (pageId) navigateTo(pageId);
    });
});

// ================================================================
// TOAST NOTIFICATION
//   Auto-hide is CSS-driven: the `.toast.show` rule chains two
//   animations — `toast-in` (0–0.22s) and `toast-out` (4.0s) —
//   so the browser's compositor handles the timer. JS only listens
//   for the `toast-out` animationend to remove the `.show` class
//   (so the element flips back to `display: none`) and continues
//   to expose hideToast() for the × button + an emergency
//   JS-level timer in case the compositor is paused.
// ================================================================
const TOAST_TTL_MS = 4000;

function showToast(title, message) {
    // Always resolve the live elements from the document, do NOT
    // use the stale DOM.toast reference taken at script load —
    // any later DOM mutation (re-render inside a page swap, etc.)
    // could leave DOM.toast pointing at a detached node.
    const t   = document.getElementById('toast');
    const tt  = document.getElementById('toastTitle');
    const tm  = document.getElementById('toastMessage');
    if (!t || !tt || !tm) return;
    tt.textContent = title   || '✅ Success';
    tm.textContent = message || 'Action completed';
    t.classList.remove('show');
    // Force a reflow so the animation re-fires when showToast is
    // called again within the same animation cycle (otherwise
    // toggling a class re-applies .show before the browser has
    // re-evaluated the animation, and the toast appears stuck).
    void t.offsetWidth;
    t.classList.add('show');

    // Belt-and-braces timer: if for any reason the `toast-out`
    // animationend doesn't fire (heavy GC, page going background,
    // animation disabled), the JS timer guarantees the class gets
    // removed.
    if (t._hideTimer) clearTimeout(t._hideTimer);
    t._hideTimer = setTimeout(() => hideToast(t), TOAST_TTL_MS);
}

function hideToast(arg) {
    // Accept either an event target OR a direct element so the
    // close button listener (whose `this` is the toast) works
    // without coupling to a captured module-level ref.
    const t = (arg && arg.nodeType === 1)
        ? arg
        : document.getElementById('toast');
    if (!t) return;
    t.classList.remove('show');
    if (t._hideTimer) { clearTimeout(t._hideTimer); t._hideTimer = null; }
}

// CSS animationend listener — primary auto-hide path.
(function bindToastAnimEnd() {
    const t = document.getElementById('toast');
    if (!t) return;
    t.addEventListener('animationend', (e) => {
        if (e.animationName === 'toast-out') hideToast(t);
    });
})();

// Close button — bind via JS AND inline onclick (the inline path
// also calls window.hideToast directly for redundancy).
DOM.toast?.addEventListener?.('click', (e) => {
    if (e.target && e.target.classList && e.target.classList.contains('toast-close')) {
        hideToast(e.currentTarget);
    }
});
