// ================================================================
// SETTINGS PAGE – Broker Configuration (popup-driven)
// ================================================================
//
// Replacing the inline 4-row form with a single "Connect Dhan"
// button + popup. Picking Dhan from the dropdown opens the popup;
// other brokers aren't wired yet (toast a friendly hint and reset).
//
// On page load we refresh the status badge so users who connect in
// one tab and come back to Settings can still see "🟢 Connected".
function toggleBrokerFields() {
    const broker = document.getElementById('brokerSelect').value;
    const dhan   = document.getElementById('dhanFields');
    const other  = document.getElementById('otherBrokerFields');

    if (broker === 'dhan') {
        dhan.style.display  = 'block';
        other.style.display = 'none';
        // NOTE: this function only toggles panel visibility. The
        // modal opens *only* via onBrokerSelectChange (deliberate
        // user pick) or the explicit "🔐 Connect Dhan" button —
        // never on the page-load boot call. Earlier the boot call
        // used to slap up the modal on every refresh because the
        // dropdown defaulted to <option value="dhan" selected>.
    } else if (broker) {
        // Untouched brokers get a friendly nudge + reset.
        if (typeof showToast === 'function') {
            showToast('🚧 Coming soon', 'Only Dhan is wired up right now.');
        }
        document.getElementById('brokerSelect').value = '';
        dhan.style.display  = 'none';
        other.style.display = 'none';
    } else {
        dhan.style.display  = 'none';
        other.style.display = 'none';
    }
}

// Fired ONLY by an explicit `<select onchange>` event — i.e., the
// user picked something. Distinct from the boot-time
// toggleBrokerFields() call so we can pop the modal on the user's
// terms and not on every page refresh.
function onBrokerSelectChange() {
    toggleBrokerFields();
    if (document.getElementById('brokerSelect').value === 'dhan') {
        openBrokerModal();
    }
}

function openBrokerModal() {
    const overlay = document.getElementById('brokerModalOverlay');
    if (!overlay) return;
    overlay.classList.add('show');
    // Focus the first input so keyboard users can type straight in.
    setTimeout(() => {
        const cid = document.getElementById('brokerClientId');
        if (cid) cid.focus();
    }, 50);
}

function closeBrokerModal() {
    const overlay = document.getElementById('brokerModalOverlay');
    if (overlay) overlay.classList.remove('show');
}

async function submitBrokerCreds() {
    const clientId   = document.getElementById('brokerClientId').value.trim();
    const totpSecret = document.getElementById('brokerTotpSecret').value.trim();
    const pin        = document.getElementById('brokerPin').value.trim();

    if (!clientId || !totpSecret) {
        if (typeof showToast === 'function') {
            showToast('⚠️ Missing fields', 'Client ID and TOTP secret are required.');
        }
        return;
    }

    const btn = document.getElementById('brokerConnectBtn');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Connecting…'; }

    try {
        const res = await fetch('/api/broker/connect', {
            method:  'POST',
            headers: {'Content-Type': 'application/json'},
            body:    JSON.stringify({
                broker:      'dhan',
                client_id:   clientId,
                totp_secret: totpSecret,
                pin:         pin,
            }),
        });
        const data = await res.json().catch(() => ({}));

        if (res.ok && data && data.connected) {
            if (typeof showToast === 'function') {
                showToast('✅ Connected', 'Dhan broker is live for this session.');
            }
            closeBrokerModal();
            // Wipe TOTP secret + PIN from the page so a screen-share
            // doesn't leak them after a successful Connect.
            document.getElementById('brokerTotpSecret').value = '';
            document.getElementById('brokerPin').value = '';
            await updateBrokerStatusBadge();
        } else {
            const detail = (data && data.detail) ? data.detail
                           : `HTTP ${res.status}`;
            if (typeof showToast === 'function') {
                showToast('❌ Connect failed', detail);
            }
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast('❌ Network error', String(e && e.message || e));
        }
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = '🔑 Connect'; }
    }
}

async function disconnectBroker() {
    try {
        const res = await fetch('/api/broker/disconnect', {method: 'POST'});
        await updateBrokerStatusBadge();
        if (typeof showToast === 'function') {
            showToast('🔌 Disconnected', res.ok
                ? 'Broker credentials cleared for this session.'
                : 'Disconnect call failed.');
        }
    } catch (e) {
        if (typeof showToast === 'function') {
            showToast('⚠️ Error', String(e && e.message || e));
        }
    }
}

async function updateBrokerStatusBadge() {
    const badge = document.getElementById('brokerStatusBadge');
    if (!badge) return;
    try {
        const res = await fetch('/api/broker/status');
        const s = await res.json();
        if (s && s.connected && s.broker === 'dhan') {
            badge.textContent = '🟢 Connected (' + (s.client_id_masked || '****') + ')';
            badge.classList.add('ok');
            badge.classList.remove('err');
        } else {
            badge.textContent = '⚪ Not connected';
            badge.classList.remove('ok', 'err');
        }
    } catch (_) {
        badge.textContent = '⚪ Status unknown';
        badge.classList.remove('ok', 'err');
    }
}

// Initial badge pull on page load.
document.addEventListener('DOMContentLoaded', () => {
    updateBrokerStatusBadge();
});

// Esc key dismisses the popup — small UX nicety.
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const overlay = document.getElementById('brokerModalOverlay');
        if (overlay && overlay.classList.contains('show')) closeBrokerModal();
    }
});
