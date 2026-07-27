/* =================================================================
 * TradeAlgo Pro · Auth (Supabase)
 * ================================================================= */

// Server-driven config (single source of truth in advance_orb/app.py).
// Hardcoded fallback used only if /api/auth/config is unreachable;
// the server already serves the same constants so both paths agree.
const AUTH_FALLBACK = {
    url: 'https://atyqkbrmrosnoczktsmm.supabase.co',
    anonKey: 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImF0eXFrYnJtcm9zbm9jemt0c21tIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODA1NjI4ODcsImV4cCI6MjA5NjEzODg4N30.f-vn85HGFfPMUNeyJLccZSIVTKvZGXp1Ty5Hw08pFsU'
};
const AUTH_CONFIG = { url: null, anonKey: null };

const EIGHT_HOURS_MS = 8 * 60 * 60 * 1000;
const DEADLINE_KEY = 'traderalgopro.auth.sessionDeadline';
const META_KEY     = 'traderalgopro.auth.user';

let supabaseClient = null;
let authMode = 'signin';
let bootstrapAuthFailed = false;

/** Pull anon URL+key from FastAPI. Returns null on a config failure;
 *  the caller must then show a clear error and not crash on
 *  `supabaseClient` being undefined. */
async function fetchAuthConfig() {
    try {
        const resp = await fetch('/api/auth/config', { credentials: 'same-origin' });
        if (!resp.ok) return null;
        const body = await resp.json();
        if (!body || !body.ok || !body.url || !body.anonKey) return null;
        return { url: body.url, anonKey: body.anonKey };
    } catch (_) {
        return null;
    }
}

/** Disable the form so a misconfigured server can't sit silently. */
function renderAuthFormDisabled(reason) {
    const form   = document.getElementById('authForm');
    const submit = document.getElementById('authSubmit');
    if (form)   form.dataset.disabled = 'true';
    if (submit) {
        submit.disabled = true;
        const t = submit.querySelector('.auth-submit-text');
        if (t) t.textContent = 'Auth unavailable';
    }
    bootstrapAuthFailed = true;
    if (reason) showAuthToast('⚠️ Auth disabled', reason, 'error', 9000);
}

async function initAuth() {
    // Always pull config from the server first; the constant is a
    // last-resort fallback if the server itself is unreachable.
    const cfg = await fetchAuthConfig();
    if (cfg) {
        AUTH_CONFIG.url     = cfg.url;
        AUTH_CONFIG.anonKey = cfg.anonKey;
    } else {
        AUTH_CONFIG.url     = AUTH_FALLBACK.url;
        AUTH_CONFIG.anonKey = AUTH_FALLBACK.anonKey;
        showAuthToast(
            '⚠️ Auth degraded',
            'Server config endpoint unreachable — using hardcoded fallback. Please contact admin if sign-in fails.',
            'warn',
            5000,
        );
    }

    if (!window.supabase || !window.supabase.createClient) {
        showAuthToast('⚠️ Auth failed', 'Supabase JS library did not load — check network.', 'error', 8000);
        renderAuthFormDisabled('Supabase JS missing');
        return;
    }

    try {
        supabaseClient = window.supabase.createClient(AUTH_CONFIG.url, AUTH_CONFIG.anonKey, {
            auth: {
                persistSession: true,
                autoRefreshToken: true,
                detectSessionInUrl: false,
                storageKey: 'traderalgopro.auth',
            },
        });
    } catch (e) {
        showAuthToast('⚠️ Auth failed', 'Could not initialise Supabase client (' + (e?.message || e) + ').', 'error', 8000);
        renderAuthFormDisabled('Supabase init threw');
        return;
    }

    attachAuthListeners();

    try {
        const { data } = await supabaseClient.auth.getSession();
        if (isBusinessSessionValid(data.session)) {
            goToDashboard();
            return;
        }
    } catch (_) {
        // getSession() throwing shouldn't break the page; fall through
        // and keep the login form rendered.
    }
}

function isBusinessSessionValid(session) {
    if (!session || !session.access_token) return false;
    const expiresAt = parseInt(localStorage.getItem(DEADLINE_KEY) || '0', 10);
    if (!Number.isFinite(expiresAt) || expiresAt <= 0) return false;
    return Date.now() < expiresAt;
}

function setBusinessDeadline() {
    localStorage.setItem(DEADLINE_KEY, String(Date.now() + EIGHT_HOURS_MS));
}

function attachAuthListeners() {
    const form         = document.getElementById('authForm');
    const submit       = document.getElementById('authSubmit');
    const submitText   = submit?.querySelector('.auth-submit-text');
    const eye          = document.getElementById('authEye');
    const tabs         = document.querySelectorAll('.auth-tab');
    const footline     = document.getElementById('authFootline');
    const forgot       = document.getElementById('authForgot');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            if (tab.dataset.mode === authMode) return;
            setAuthMode(tab.dataset.mode, { tabs, footline, submitText });
        });
    });

    const switcher = document.getElementById('authSwitchMode');
    if (switcher) {
        switcher.addEventListener('click', e => {
            e.preventDefault();
            setAuthMode(authMode === 'signin' ? 'signup' : 'signin', { tabs, footline, submitText });
        });
    }

    forgot?.addEventListener('click', async e => {
        e.preventDefault();
        const email = (document.getElementById('authEmail')?.value || '').trim();
        if (!email) {
            showAuthToast('📩 Add email first', 'Enter your email above, then click Forgot.', 'warn');
            return;
        }
        try {
            submit.disabled = true;
            const { error } = await supabaseClient.auth.resetPasswordForEmail(email, {
                redirectTo: window.location.origin + '/',
            });
            if (error) throw error;
            showAuthToast('📩 Reset link sent', 'Check your inbox for the Supabase reset email.', 'success', 7000);
        } catch (err) {
            showAuthToast('⚠️ Reset failed', err.message || 'Try again in a moment.', 'error');
        } finally {
            submit.disabled = false;
        }
    });

    eye?.addEventListener('click', () => {
        const input = document.getElementById('authPassword');
        if (!input) return;
        const isShowing = input.type === 'text';
        input.type = isShowing ? 'password' : 'text';
        eye.textContent = isShowing ? '👁️' : '🙈';
        eye.setAttribute('aria-label', isShowing ? 'Show password' : 'Hide password');
    });

    form?.addEventListener('submit', async e => {
        e.preventDefault();
        if (!supabaseClient) {
            showAuthToast('⚠️ Auth not ready', 'Please refresh and try again.', 'error');
            return;
        }

        const emailEl    = document.getElementById('authEmail');
        const passEl     = document.getElementById('authPassword');
        const userEl     = document.getElementById('authUsername');
        const email      = (emailEl?.value || '').trim();
        const password   = (passEl?.value || '');
        const username   = (userEl?.value || '').trim();

        if (!email || !email.includes('@')) {
            showAuthToast('📧 Invalid email', 'Please enter a valid email address.', 'warn');
            emailEl?.focus();
            return;
        }
        if (!password || password.length < 6) {
            showAuthToast('🔑 Password too short', 'Use at least 6 characters.', 'warn');
            passEl?.focus();
            return;
        }

        submit.classList.add('loading');
        submit.disabled = true;

        try {
            let result;
            if (authMode === 'signup') {
                if (!username || !/^[a-zA-Z0-9_]{3,20}$/.test(username)) {
                    showAuthToast('👤 Pick a username', '3–20 characters (letters, digits, underscore).', 'warn');
                    userEl?.focus();
                    return;
                }
                result = await supabaseClient.auth.signUp({
                    email,
                    password,
                    options: { data: { username } }
                });
            } else {
                result = await supabaseClient.auth.signInWithPassword({
                    email,
                    password
                });
            }

            // DEBUG: log full result to console
            console.log('[auth] Supabase response:', result);

            if (result.error) {
                throw result.error;
            }

            const session = result.data?.session;
            const user    = result.data?.user;

            if (!session && user) {
                showAuthToast('📬 Confirm your email',
                    `Check ${user.email} for the confirmation link.`,
                    'success', 9000);
                return;
            }
            if (!session) {
                throw new Error('No session established');
            }

            setBusinessDeadline();
            if (user?.email) localStorage.setItem(META_KEY, user.email);
            showAuthToast('✅ Signed in', 'Opening your dashboard…', 'success', 1500);
            setTimeout(goToDashboard, 350);

        } catch (err) {
            console.error('[auth] Error:', err);
            // Extract error message from various possible sources
            let msg = err?.message || err?.error_description || err?.details || 'Unknown error';
            if (typeof err === 'object' && err !== null && err.msg) msg = err.msg;
            if (typeof err === 'string') msg = err;
            showAuthToast('⚠️ Sign-in failed', humaniseAuthError(msg), 'error', 7000);
        } finally {
            submit.classList.remove('loading');
            submit.disabled = false;
        }
    });
}

function setAuthMode(next, refs) {
    authMode = next;
    const tabs = refs.tabs || document.querySelectorAll('.auth-tab');
    tabs.forEach(t => {
        const active = t.dataset.mode === next;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', String(active));
    });
    const form = document.getElementById('authForm');
    if (form) form.dataset.mode = next;
    if (refs.submitText) refs.submitText.textContent = next === 'signup' ? 'Create Account' : 'Sign In';

    const heading = document.querySelector('.auth-heading');
    const sub     = document.getElementById('authModeHint');
    if (heading) heading.textContent = next === 'signup' ? 'Create your account' : 'Welcome back';
    if (sub) sub.textContent = next === 'signup'
        ? '8-hour session, no password re-prompts.'
        : 'Sign in to continue to your screener & auto-buy desk.';

    const usernameField = document.getElementById('authUsernameField');
    if (usernameField) {
        usernameField.style.display = next === 'signup' ? 'block' : 'none';
    }

    if (refs.footline) {
        refs.footline.innerHTML = next === 'signup'
            ? 'Already have an account? <button type="button" class="auth-link-inline" id="authSwitchMode">Sign in</button>'
            : 'New here? <button type="button" class="auth-link-inline" id="authSwitchMode">Create an account</button>';
        const newSwitcher = document.getElementById('authSwitchMode');
        if (newSwitcher) {
            newSwitcher.addEventListener('click', e => {
                e.preventDefault();
                setAuthMode(authMode === 'signin' ? 'signup' : 'signin', {
                    tabs: document.querySelectorAll('.auth-tab'),
                    footline: document.getElementById('authFootline'),
                    submitText: document.querySelector('.auth-submit-text')
                });
            });
        }
    }
}

function humaniseAuthError(raw) {
    // The Supabase JS client occasionally returns an AuthRetryableFetchError
    // whose .message is the literal string "{}" because the upstream
    // GoTrue server replied with HTTP 500 and no body. Detect that case
    // and show a useful message instead of "{}".
    if (typeof raw !== 'string') raw = String(raw);
    const trimmed = raw.trim();
    if (trimmed === '{}' || trimmed === '' || trimmed === 'null' || trimmed === 'undefined') {
        return 'Auth service returned no details (HTTP 500). Please retry in a moment — if it keeps failing, contact admin.';
    }
    if (/Invalid login credentials/i.test(raw))         return 'Wrong email or password.';
    if (/User already registered/i.test(raw))           return 'That email is already in use — try Sign In instead.';
    if (/Password should be at least/i.test(raw))       return 'Password must be at least 6 characters.';
    if (/Email not confirmed/i.test(raw))               return 'Confirm your email first — check your inbox.';
    if (/rate limit/i.test(raw))                        return 'Too many attempts. Try again in a minute.';
    if (/Network|Failed to fetch/i.test(raw))           return 'Network error — check your connection.';
    if (/AuthRetryableFetchError/i.test(raw))           return 'Auth service temporarily unavailable. Retry in a moment.';
    if (/No session/i.test(raw))                        return 'Session error — please try again.';
    return raw;
}

function goToDashboard() {
    window.location.assign('/?authed=1');
}

function showAuthToast(title, sub, kind, ms) {
    const host = document.getElementById('authToastHost');
    if (!host) return;
    const node = document.createElement('div');
    node.className = 'toast toast-' + (kind || 'info');
    node.innerHTML = '<div class="toast-title">' + escapeHtml(title) + '</div>'
                   + (sub ? '<div class="toast-sub">' + escapeHtml(sub) + '</div>' : '');
    host.appendChild(node);
    setTimeout(() => {
        node.classList.add('toast-leaving');
        setTimeout(() => node.remove(), 350);
    }, ms || 4500);
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
}

document.addEventListener('DOMContentLoaded', () => {
    initAuth().catch(err => {
        console.error('[auth] init failed:', err);
        showAuthToast('⚠️ Auth failed', 'Could not initialise — refresh to retry.', 'error');
    });
});