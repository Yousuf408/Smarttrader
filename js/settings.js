// ================================================================
// SETTINGS PAGE – Broker Configuration (Dhan + Angel One)
// ================================================================

function toggleBrokerFields() {
    const broker = document.getElementById('brokerSelect').value;
    const dhanFields = document.getElementById('dhanFields');
    const angelFields = document.getElementById('angelFields');
    const otherFields = document.getElementById('otherBrokerFields');

    // Hide all first
    if (dhanFields) dhanFields.style.display = 'none';
    if (angelFields) angelFields.style.display = 'none';
    if (otherFields) otherFields.style.display = 'none';

    // Show relevant fields
    if (broker === 'dhan') {
        if (dhanFields) dhanFields.style.display = 'block';
        document.getElementById('brokerModalName').textContent = 'Dhan';
        document.getElementById('brokerModalTitle').innerHTML = '🔐 Connect to <span id="brokerModalName">Dhan</span>';
        // Show Dhan fields in modal, hide Angel fields
        document.getElementById('modalDhanFields').style.display = 'block';
        document.getElementById('modalAngelFields').style.display = 'none';
        // Update status badge
        updateBrokerStatusBadge('dhan');
    } else if (broker === 'angel') {
        if (angelFields) angelFields.style.display = 'block';
        document.getElementById('brokerModalName').textContent = 'Angel One';
        document.getElementById('brokerModalTitle').innerHTML = '🔐 Connect to <span id="brokerModalName">Angel One</span>';
        // Show Angel fields in modal, hide Dhan fields
        document.getElementById('modalDhanFields').style.display = 'none';
        document.getElementById('modalAngelFields').style.display = 'block';
        // Update status badge
        updateBrokerStatusBadge('angel');
    } else if (broker) {
        // Untouched brokers get a friendly nudge + reset
        if (typeof showToast === 'function') {
            showToast('🚧 Coming soon', 'Only Dhan and Angel One are wired up right now.');
        }
        document.getElementById('brokerSelect').value = '';
        if (otherFields) otherFields.style.display = 'none';
    } else {
        if (otherFields) otherFields.style.display = 'none';
    }
}

// Fired ONLY by an explicit `<select onchange>` event
function onBrokerSelectChange() {
    toggleBrokerFields();
    const broker = document.getElementById('brokerSelect').value;
    if (broker === 'dhan' || broker === 'angel') {
        openBrokerModal();
    }
}

function openBrokerModal() {
    const overlay = document.getElementById('brokerModalOverlay');
    if (!overlay) return;
    overlay.classList.add('show');
    // Focus the first input so keyboard users can type straight in.
    setTimeout(() => {
        const broker = document.getElementById('brokerSelect').value;
        if (broker === 'dhan') {
            const cid = document.getElementById('brokerClientId');
            if (cid) cid.focus();
        } else if (broker === 'angel') {
            const apiKey = document.getElementById('brokerApiKey');
            if (apiKey) apiKey.focus();
        }
    }, 50);
}

function closeBrokerModal() {
    const overlay = document.getElementById('brokerModalOverlay');
    if (overlay) overlay.classList.remove('show');
}

async function submitBrokerCreds() {
    const broker = document.getElementById('brokerSelect').value;
    const btn = document.getElementById('brokerConnectBtn');

    if (!broker) {
        if (typeof showToast === 'function') {
            showToast('⚠️ No broker selected', 'Please select a broker first.');
        }
        return;
    }

    let payload = { broker: broker };

    if (broker === 'dhan') {
        const clientId = document.getElementById('brokerClientId').value.trim();
        const totpSecret = document.getElementById('brokerTotpSecret').value.trim();
        const pin = document.getElementById('brokerPin').value.trim();

        if (!clientId || !totpSecret) {
            if (typeof showToast === 'function') {
                showToast('⚠️ Missing fields', 'Client ID and TOTP secret are required.');
            }
            return;
        }

        payload.client_id = clientId;
        payload.totp_secret = totpSecret;
        payload.pin = pin || undefined;

    } else if (broker === 'angel') {
        const apiKey = document.getElementById('brokerApiKey').value.trim();
        const clientId = document.getElementById('brokerAngelClientId').value.trim();
        const password = document.getElementById('brokerPassword').value.trim();
        const totp = document.getElementById('brokerAngelTotp').value.trim();

        if (!apiKey || !clientId || !password) {
            if (typeof showToast === 'function') {
                showToast('⚠️ Missing fields', 'API Key, Client ID, and Password are required.');
            }
            return;
        }

        payload.api_key = apiKey;
        payload.client_id = clientId;
        payload.password = password;
        payload.totp_secret = totp || undefined;

    } else {
        if (typeof showToast === 'function') {
            showToast('⚠️ Unsupported broker', 'Please select a supported broker.');
        }
        return;
    }

    if (btn) { 
        btn.disabled = true; 
        btn.textContent = '⏳ Connecting…'; 
    }

    // Broker auth goes through a WAF proxy and can take ~90s; never let the
    // button hang forever on a stalled request.
    const controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    const abortTimer = controller ? setTimeout(() => controller.abort(), 180000) : null;
    try {
        const res = await fetch('/api/broker/connect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
            signal: controller ? controller.signal : undefined,
        });
        const data = await res.json().catch(() => ({}));

        if (res.ok && data && data.connected) {
            const brokerName = broker === 'angel' ? 'Angel One' : 
                              broker === 'dhan' ? 'Dhan' : broker;
            if (typeof showToast === 'function') {
                showToast('✅ Connected', `${brokerName} broker is live for this session.`);
            }
            closeBrokerModal();
            // Clear sensitive fields
            clearBrokerFields(broker);
            await updateBrokerStatusBadge(broker);
        } else {
            const detail = (data && data.detail) ? data.detail : `HTTP ${res.status}`;
            if (typeof showToast === 'function') {
                showToast('❌ Connect failed', detail);
            }
        }
    } catch (e) {
        const timedOut = controller && e && e.name === 'AbortError';
        if (typeof showToast === 'function') {
            showToast(
                timedOut ? '⏱️ Connect timed out' : '❌ Network error',
                timedOut ? 'Broker auth took too long — check server logs and retry.' : String(e && e.message || e)
            );
        }
    } finally {
        if (abortTimer) clearTimeout(abortTimer);
        if (btn) {
            btn.disabled = false;
            btn.textContent = '🔑 Connect';
        }
    }
}

// ================================================================
// SMART PASTE — paste into API Key → auto-fill remaining fields
// Accepts newline-separated or space-separated credentials.
// ================================================================
document.addEventListener('DOMContentLoaded', () => {
    const apiKeyInput = document.getElementById('brokerApiKey');
    if (apiKeyInput) {
        apiKeyInput.addEventListener('paste', (e) => {
            // Let the paste happen naturally first, then check content
            requestAnimationFrame(() => {
                const raw = apiKeyInput.value.trim();
                if (!raw) return;

                let parts;

                // Prefer newline split; fall back to space split when
                // the pasted string has 3+ space-delimited tokens.
                if (raw.includes('\n')) {
                    parts = raw.split('\n').map(s => s.trim()).filter(Boolean);
                } else {
                    const spaced = raw.split(/\s+/).filter(Boolean);
                    if (spaced.length >= 3) {
                        parts = spaced;
                    }
                }

                if (!parts || parts.length < 2) return;

                const fields = [
                    'brokerApiKey',
                    'brokerAngelClientId',
                    'brokerPassword',
                    'brokerAngelTotp',
                ];

                // Set API Key to the first value only
                apiKeyInput.value = parts[0];

                // Distribute remaining values to the next fields
                for (let i = 1; i < parts.length && i < fields.length; i++) {
                    const el = document.getElementById(fields[i]);
                    if (el) el.value = parts[i];
                }

                // Flash a brief toast
                if (typeof showToast === 'function') {
                    const count = Math.min(parts.length, fields.length);
                    showToast('📋 Auto-filled', `${count} fields populated from paste`);
                }
            });
        });
    }
});

function clearBrokerFields(broker) {
    if (broker === 'dhan') {
        document.getElementById('brokerClientId').value = '';
        document.getElementById('brokerTotpSecret').value = '';
        document.getElementById('brokerPin').value = '';
    } else if (broker === 'angel') {
        document.getElementById('brokerApiKey').value = '';
        document.getElementById('brokerAngelClientId').value = '';
        document.getElementById('brokerPassword').value = '';
        document.getElementById('brokerAngelTotp').value = '';
    }
}

async function disconnectBroker() {
    try {
        const res = await fetch('/api/broker/disconnect', { method: 'POST' });
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

async function updateBrokerStatusBadge(broker = null) {
    const badgeDhan = document.getElementById('brokerStatusBadge');
    const badgeAngel = document.getElementById('brokerStatusBadgeAngel');

    // If specific broker requested, update only that badge
    if (broker === 'dhan' && badgeDhan) {
        await fetchBrokerStatus('dhan', badgeDhan);
        return;
    } else if (broker === 'angel' && badgeAngel) {
        await fetchBrokerStatus('angel', badgeAngel);
        return;
    }

    // Update both badges
    if (badgeDhan) await fetchBrokerStatus('dhan', badgeDhan);
    if (badgeAngel) await fetchBrokerStatus('angel', badgeAngel);
}

async function fetchBrokerStatus(broker, badge) {
    try {
        const res = await fetch('/api/broker/status');
        const s = await res.json();

        if (s && s.connected && s.broker === broker) {
            const brokerName = broker === 'angel' ? 'Angel One' : 
                              broker === 'dhan' ? 'Dhan' : broker;
            badge.textContent = `🟢 Connected to ${brokerName} (${s.client_id_masked || '****'})`;
            badge.classList.add('ok');
            badge.classList.remove('err');
        } else {
            const brokerName = broker === 'angel' ? 'Angel One' : 
                              broker === 'dhan' ? 'Dhan' : broker;
            badge.textContent = `⚪ ${brokerName} not connected`;
            badge.classList.remove('ok', 'err');
        }
    } catch (_) {
        badge.textContent = '⚪ Status unknown';
        badge.classList.remove('ok', 'err');
    }
}

// Initial badge pull + auto-select connected broker on page load.
document.addEventListener('DOMContentLoaded', async () => {
    // Fetch which broker is connected
    try {
        const resp = await fetch('/api/broker/status');
        const status = await resp.json();
        if (status && status.connected && status.broker) {
            const select = document.getElementById('brokerSelect');
            if (select) {
                select.value = status.broker;
                toggleBrokerFields();
            }
        }
    } catch (_) {
        // Server not reachable — leave dropdown as-is
    }
    updateBrokerStatusBadge();
    updateCacheStatus();
});

// Esc key dismisses the popup — small UX nicety.
document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
        const overlay = document.getElementById('brokerModalOverlay');
        if (overlay && overlay.classList.contains('show')) closeBrokerModal();
    }
});

// ================================================================
// HELPER FUNCTIONS FOR HTML ELEMENTS
// ================================================================

// Check if broker is connected (for other parts of the app)
function isBrokerConnected(broker = null) {
    // This will be used by other modules
    return new Promise(async (resolve) => {
        try {
            const res = await fetch('/api/broker/status');
            const s = await res.json();
            if (broker) {
                resolve(s && s.connected && s.broker === broker);
            } else {
                resolve(s && s.connected);
            }
        } catch (_) {
            resolve(false);
        }
    });
}

// ================================================================
// STRATEGY CACHE STATUS INDICATOR
// ================================================================

async function updateCacheStatus() {
    const el = document.getElementById('cacheStatusIndicator');
    if (!el) return;
    try {
        const res = await fetch('/api/cache/status');
        const s = await res.json();
        if (s.is_stale) {
            el.textContent = `🟡 Stale — ${s.symbol_count || 0} stocks (rebuilding…)`;
            el.style.color = '#f59e0b';
        } else {
            const updated = s.last_updated ? new Date(s.last_updated).toLocaleString('en-IN', {
                day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit'
            }) : 'unknown';
            el.textContent = `🟢 Fresh — ${s.symbol_count} stocks · updated ${updated}`;
            el.style.color = '#22c55e';
        }
    } catch (_) {
        el.textContent = '⚪ Cache status unavailable';
        el.style.color = '#666';
    }
}

// ================================================================
// MANUAL CANDLE FETCH — "Get Data" button (5-min + 15-min)
// ================================================================

let _manualFetchTimer = null;

async function startManualFetch() {
    const btn = document.getElementById('manualFetchBtn');
    const status = document.getElementById('manualFetchStatus');
    const bar = document.getElementById('manualFetchBar');
    if (!btn || !status || !bar) return;
    try {
        const res = await fetch('/api/candles/manual-fetch/start', { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (data && data.already_running) {
            setFetchStatus('🟡 A fetch is already running…');
            _pollManualFetch();
            return;
        }
        if (!res.ok) {
            setFetchStatus('🔴 ' + ((data && data.detail) || 'Failed to start'));
            return;
        }
        setFetchStatus('🟢 Fetch started — polling progress…');
        bar.style.width = '0%';
        _pollManualFetch();
    } catch (e) {
        setFetchStatus('🔴 Network error starting fetch');
    }
}

function setFetchStatus(text) {
    const status = document.getElementById('manualFetchStatus');
    if (status) status.textContent = text;
}

function _pollManualFetch() {
    if (_manualFetchTimer) clearInterval(_manualFetchTimer);
    _manualFetchTimer = setInterval(async () => {
        const bar = document.getElementById('manualFetchBar');
        const status = document.getElementById('manualFetchStatus');
        try {
            const res = await fetch('/api/candles/manual-fetch/status');
            if (!res.ok) throw new Error(res.status);
            const s = await res.json();
            if (!bar || !status) return;

            if (s && s.running) {
                const pct = (s.total > 0) ? Math.min(100, Math.round(s.done / s.total * 100)) : 5;
                bar.style.width = pct + '%';
                status.textContent = `${s.phase === '15' ? '15-min' : '5-min'} candles — ${s.done}/${s.total} (${pct}%) · last: ${s.current_symbol || '-'} · saved: ${s.saved}`;
            } else {
                // finished (or idle) — show final result once, then stop polling
                clearInterval(_manualFetchTimer);
                _manualFetchTimer = null;
                if (s && s.finished_at) {
                    bar.style.width = '100%';
                    setFetchStatus(`✅ ${s.last_message || 'Done'} — saved ${s.saved} rows, ${s.errors} errors`);
                } else {
                    bar.style.width = '0%';
                    setFetchStatus('Idle.');
                }
            }
        } catch (e) {
            clearInterval(_manualFetchTimer);
            _manualFetchTimer = null;
            setFetchStatus('⚪ Progress unavailable (check server logs)');
        }
    }, 2500);
}

// get current broker info
async function getCurrentBroker() {
    try {
        const res = await fetch('/api/broker/status');
        const s = await res.json();
        return s || { connected: false };
    } catch (_) {
        return { connected: false };
    }
}