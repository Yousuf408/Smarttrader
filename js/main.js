// ================================================================
// GLOBAL STATE & DOM
// ================================================================
const DOM = {
    pages: document.querySelectorAll('.page'),
    navLinks: document.querySelectorAll('.nav-links a'),
    toast: document.getElementById('toast'),
    toastTitle: document.getElementById('toastTitle'),
    toastMessage: document.getElementById('toastMessage'),
    modalOverlay: document.getElementById('modalOverlay'),
    modalTitle: document.getElementById('modalTitle'),
    modalStrategyName: document.getElementById('modalStrategyName'),
    modalStrategyDesc: document.getElementById('modalStrategyDesc'),
    modalEntryRule: document.getElementById('modalEntryRule'),
    modalRisk: document.getElementById('modalRisk')
};

let autoBuyEnabled = false;

// ================================================================
// HAMBURGER TOGGLE — mobile responsive menu
// ================================================================
document.addEventListener('DOMContentLoaded', () => {
    const btn = document.getElementById('hamburgerBtn');
    const navLinks = document.getElementById('navLinks');
    if (btn && navLinks) {
        btn.addEventListener('click', (e) => {
            e.stopPropagation();
            navLinks.classList.toggle('open');
        });
        navLinks.querySelectorAll('a').forEach(a => {
            a.addEventListener('click', () => {
                navLinks.classList.remove('open');
            });
        });
        document.addEventListener('click', (e) => {
            if (!navLinks.contains(e.target) && !btn.contains(e.target)) {
                navLinks.classList.remove('open');
            }
        });
    }
    // Init theme from localStorage
    applyTheme(localStorage.getItem('tradetheme') || 'light');
    // Start market ribbon updates
    updateMarketRibbon();
    setInterval(updateMarketRibbon, 10000);
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
            'g': 'strategies',
            'b': 'backtest',
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
        // Alt+N = New strategy (strategies page)
        if (key === 'n') {
            e.preventDefault();
            openModal('new');
        }
        // Alt+Esc = close modal
        if (key === 'Escape') closeModal();
    }
});

// ================================================================
// MARKET RIBBON — live indices from SSE
// ================================================================
let marketRibbonData = { sensex: null, nifty: null, banknifty: null, vix: null };

function updateMarketRibbon() {
    // Fetch from backend proxy (avoids CORS)
    fetch('/api/market/indices')
    .then(r => r.json())
    .then(data => {
        const indices = data.data || [];
        const find = (name) => indices.find(i => i.index === name);
        const nifty = find('NIFTY 50');
        const banknifty = find('NIFTY BANK');
        const vix = find('INDIA VIX');

        const setRibbon = (id, item) => {
            const el = document.getElementById(id);
            if (!el || !item) return;
            const last = item.last || 0;
            const chg = item.change;
            const pct = item.pChange;
            const valEl = el.querySelector('.value');
            if (!valEl) return;
            // Handle null/None/undefined when market is closed
            if (chg == null || pct == null) {
                valEl.textContent = last.toLocaleString('en-IN');
                valEl.className = 'value';
                return;
            }
            const chgNum = Number(chg);
            const pctNum = Number(pct);
            if (isNaN(chgNum) || isNaN(pctNum)) {
                valEl.textContent = last.toLocaleString('en-IN');
                valEl.className = 'value';
                return;
            }
            const arrow = chgNum >= 0 ? '▲' : '▼';
            const cls = chgNum >= 0 ? 'up' : 'down';
            valEl.textContent = `${last.toLocaleString('en-IN')} ${arrow} ${Math.abs(pctNum).toFixed(2)}%`;
            valEl.className = `value ${cls}`;
        };

        setRibbon('ribbon-nifty', nifty);
        setRibbon('ribbon-banknifty', banknifty);
        setRibbon('ribbon-vix', vix);

        // Session timer
        updateSessionTimer();
    })
    .catch(() => {
        // Silently fall back — ribbon will show "—"
    });
}

function updateSessionTimer() {
    const el = document.getElementById('ribbonSession');
    if (!el) return;
    const now = new Date();
    const hour = now.getHours();
    const min = now.getMinutes();
    const istHour = (hour + 5 + Math.floor((min + 30) / 60)) % 24;
    const istMin = (min + 30) % 60;
    // Market hours: 9:15 to 15:30 IST
    const marketOpen = (istHour > 9 || (istHour === 9 && istMin >= 15));
    const marketClose = (istHour < 15 || (istHour === 15 && istMin <= 30));
    if (marketOpen && marketClose) {
        const closeMin = (15 * 60 + 30) - (istHour * 60 + istMin);
        const h = Math.floor(closeMin / 60);
        const m = closeMin % 60;
        el.textContent = `📈 ${h}h ${m}m left`;
    } else if (istHour < 9 || (istHour === 9 && istMin < 15)) {
        const openMin = (9 * 60 + 15) - (istHour * 60 + istMin);
        const h = Math.floor(openMin / 60);
        const m = openMin % 60;
        el.textContent = `🔴 Opens in ${h}h ${m}m`;
    } else {
        el.textContent = '🔴 Market Closed';
    }
}

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
// NAVIGATION
// ================================================================
function navigateTo(pageId) {
    DOM.navLinks.forEach(a => a.classList.remove('active'));
    const activeLink = document.querySelector(`.nav-links a[data-page="${pageId}"]`);
    if (activeLink) activeLink.classList.add('active');
    
    DOM.pages.forEach(p => p.classList.remove('active'));
    const targetPage = document.getElementById('page-' + pageId);
    if (targetPage) targetPage.classList.add('active');
    
    // Auto-refresh (advanceorb) keeps running even when the user is on other pages,
    // so the screener data stays current for the upcoming auto-buy feature.

    if (pageId === 'home') loadHome();
    else if (pageId === 'strategies') loadStrategies();
    else if (pageId === 'portfolio') loadPortfolio();
    else if (pageId === 'testing') loadTesting();
}

document.querySelectorAll('.nav-links a').forEach(link => {
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

// ================================================================
// MODAL FUNCTIONALITY
// ================================================================
function openModal(action) {
    DOM.modalOverlay.classList.add('show');
    if (action === 'new') {
        DOM.modalTitle.textContent = '➕ Create New Strategy';
        DOM.modalStrategyName.value = 'New Strategy';
        DOM.modalStrategyDesc.value = 'Describe your strategy rules...';
    } else {
        DOM.modalTitle.textContent = '✏️ Edit Strategy';
        DOM.modalStrategyName.value = 'Advance ORB';
        DOM.modalStrategyDesc.value = 'Opening Range Breakout Strategy';
    }
}

function closeModal() {
    DOM.modalOverlay.classList.remove('show');
}

DOM.modalOverlay.addEventListener('click', function(e) {
    if (e.target === this) closeModal();
});

document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') closeModal();
});
