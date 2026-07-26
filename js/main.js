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
        columns: ['Symbol', 'Price', 'CHG%', 'Breakout', 'Support Price', 'MaxQty'],
        data: [
            { symbol: 'RELIANCE', price: '2856.40', change: '+2.1%', breakout: 'Active', supportPrice: '2,800.00', maxQty: '100' },
            { symbol: 'TCS', price: '3920.00', change: '+0.8%', breakout: 'Waiting', supportPrice: '3,850.00', maxQty: '50' },
            { symbol: 'INFY', price: '1545.00', change: '+3.4%', breakout: 'Active', supportPrice: '1,500.00', maxQty: '75' },
            { symbol: 'HDFC', price: '1680.00', change: '-1.2%', breakout: 'Waiting', supportPrice: '1,650.00', maxQty: '60' },
            { symbol: 'SBIN', price: '785.00', change: '+1.8%', breakout: 'Active', supportPrice: '760.00', maxQty: '120' },
            { symbol: 'BHARTI', price: '1234.00', change: '+0.3%', breakout: 'Waiting', supportPrice: '1,200.00', maxQty: '40' }
        ]
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
    
    if (pageId !== 'screener' && typeof window.stopAdvanceOrbAutoRefresh === 'function') {
        window.stopAdvanceOrbAutoRefresh();
    }

    if (pageId === 'home') loadHome();
    else if (pageId === 'strategies') loadStrategies();
    else if (pageId === 'portfolio') loadPortfolio();
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
// ================================================================
let toastTimeout = null;

function showToast(title, message) {
    DOM.toastTitle.textContent = title || '✅ Success';
    DOM.toastMessage.textContent = message || 'Action completed';
    DOM.toast.classList.add('show');
    clearTimeout(toastTimeout);
    toastTimeout = setTimeout(() => DOM.toast.classList.remove('show'), 4000);
}

function hideToast() {
    DOM.toast.classList.remove('show');
    clearTimeout(toastTimeout);
}

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
