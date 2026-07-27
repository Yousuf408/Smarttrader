/* =================================================================
 * TradeAlgo Pro · Auth (Supabase)
 * ----------------------------------------------------------------
 * Goals (user-friendly priority order):
 *   1. Sign in / sign up via Supabase email + password.
 *   2. Session persists across reloads and tab changes for 8 hours.
 *   3. After 8 hours -> local sign-out, force re-login.
 *   4. While session is valid, redirect to / (the dashboard shell).
 *   5. Cleanly render errors, not throw silent failures.
 * =================================================================*/

/* ----------------------------------------------------------------
 * 1. CONFIG (served by /api/auth/config from the FastAPI app).
 *    Falls back gracefully if config is missing.
 * ----------------------------------------------------------------*/
const AUTH_CONFIG = { url: null, anonKey: null };

/* In-memory cached expiry so we don't have to do Date.parse on every
 * tab focus. Supabase itself persists tokens via localStorage; our
 * 8-hour business clock is layered on top. */
const EIGHT_HOURS_MS = 8 * 60 * 60 * 1000;

/* ----------------------------------------------------------------
 * 2. STATE
 * ----------------------------------------------------------------*/
// Renamed from `supabase` to `supabaseClient` so we don't collide with
// the UMD global that the Supabase CDN script attaches to window.
let supabaseClient = null;
let authMode = 'signin';            // 'signin' | 'signup'

/* ----------------------------------------------------------------
 * 3. BOOT
 * ----------------------------------------------------------------*/
async function initAuth() {
    const cfg = await fetchAuthConfig();

    if (!cfg) {
        showAuthToast('⚠️ Auth disabled', 'Server returned no Supabase config — admin must set SUPABASE_URL + SUPABASE_ANON_KEY.', 'error', 9000);
        renderAuthFormDisabled();
        return;
    }

    AUTH_CONFIG.url = cfg.url;
    AUTH_CONFIG.anonKey = cfg.anonKey;

    if (!window.supabase || !window.supabase.createClient) {
        showAuthToast('⚠️ Auth failed', 'Supabase JS did not load — check your network.', 'error');
        return;
    }

    supabaseClient = window.supabase.createClient(AUTH_CONFIG.url, AUTH_CONFIG.anonKey, {
        auth: {
            persistSession: true,            // localStorage cross-tab persistence
            autoRefreshToken: true,
            detectSessionInUrl: false,
            storageKey: 'traderalgopro.auth',
        },
    });

    attachAuthListeners();

    // If a session is already present and still within the 8h window,
    // skip the login screen entirely and route the user to the
    // dashboard shell.
    const existing = await supabaseClient.auth.getSession();
    if (isBusinessSessionValid(existing.data.session)) {
        goToDashboard();
        return;
    }
}

async function fetchAuthConfig() {
    try {
        const r = await fetch('/api/auth/config', { credentials: 'omit', cache: 'no-store' });
        if (!r.ok) return null;
        const j = await r.json();
        if (!j || !j.url || !j.anonKey) return null;
        return j;
    } catch (_) {
        return null;
    }
}

/* 8-hour business clock. We compute an absolute deadline at sign-in
 * time and store it in localStorage. On every boot we check whether
 * (now < deadline). If expired, we wipe both Supabase session and our
 * deadline, and stay on /login. */
const DEADLINE_KEY = 'traderalgopro.auth.sessionDeadline';
const META_KEY     = 'traderalgopro.auth.user';

function isBusinessSessionValid(session) {
    if (!session || !session.access_token) return false;

    const expiresAt = parseInt(localStorage.getItem(DEADLINE_KEY) || '0', 10);
    if (!Number.isFinite(expiresAt) || expiresAt <= 0) return false;
    return Date.now() < expiresAt;
}

function setBusinessDeadline() {
    localStorage.setItem(DEADLINE_KEY, String(Date.now() + EIGHT_HOURS_MS));
}

function clearBusinessSession() {
    localStorage.removeItem(DEADLINE_KEY);
    localStorage.removeItem(META_KEY);
}

/* ----------------------------------------------------------------
 * 4. EVENT WIRING
 * ----------------------------------------------------------------*/
function attachAuthListeners() {
    const form         = document.getElementById('authForm');
    const submit       = document.getElementById('authSubmit');
    const submitText   = submit?.querySelector('.auth-submit-text');
    const eye          = document.getElementById('authEye');
    const switcher     = document.getElementById('authSwitchMode');
    const tabs         = document.querySelectorAll('.auth-tab');
    const footline     = document.getElementById('authFootline');
    const forgot       = document.getElementById('authForgot');

    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            if (tab.dataset.mode === authMode) return;
            setAuthMode(tab.dataset.mode, { tabs, footline, submitText });
        });
    });

    switcher?.addEventListener('click', e => {
        e.preventDefault();
        setAuthMode(authMode === 'signin' ? 'signup' : 'signin', { tabs, footline, submitText });
    });

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
        if (!supabaseClient) return;

        const emailEl    = document.getElementById('authEmail');
        const passEl     = document.getElementById('authPassword');
        const userEl     = document.getElementById('authUsername');
        const emailRaw   = (emailEl?.value || '').trim();
        const pass       = (passEl?.value || '');
        const username   = authMode === 'signup' ? (userEl?.value || '').trim() : '';

        // ── client-side validation ──────────────────────────────────────
        if (authMode === 'signup') {
            if (!/^[a-zA-Z0-9_]{3,20}$/.test(username)) {
                showAuthToast('👤 Pick a username',
                    'Username must be 3–20 characters (letters, digits, underscore).',
                    'warn');
                if (userEl) userEl.focus();
                return;
            }
            if (!emailRaw.includes('@')) {
                showAuthToast('📧 Email looks wrong', 'Add a valid email above.', 'warn');
                if (emailEl) emailEl.focus();
                return;
            }
        }
        if (!pass || pass.length < 6) {
            showAuthToast('🔑 Password too short', 'Use at least 6 characters.', 'warn');
            return;
        }

        submit.classList.add('loading');
        submit.disabled = true;
        try {
            // Sign In: first field can be email OR username.
            let loginEmail = emailRaw;
            if (authMode === 'signin' && !emailRaw.includes('@')) {
                const lr = await fetch(
                    '/api/auth/lookup-username?u=' + encodeURIComponent(emailRaw),
                    { cache: 'no-store' }
                );
                const lrJson = await lr.json().catch(() => ({}));
                if (!lr.ok) throw new Error(lrJson.detail || 'Username not found.');
                if (!lrJson.email) throw new Error('Email not provisioned for that username.');
                loginEmail = lrJson.email;
            }

            let result;
            if (authMode === 'signup') {
                result = await supabaseClient.auth.signUp({ email: emailRaw, password: pass });
            } else {
                result = await supabaseClient.auth.signInWithPassword({ email: loginEmail, password: pass });
            }
            if (result.error) throw result.error;
            const session = result.data?.session;
            const user    = result.data?.user;
            if (!session && user) {
                showAuthToast('📬 Confirm your email',
                    'Check ' + user.email + ' for the confirmation link.',
                    'success', 9000);
                return;
            }
            if (!session) {
                showAuthToast('⚠️ No session', 'Could not establish a session, try again.', 'error');
                return;
            }

            // Persist the username on first signup. (Refresh / token
            // roll-over does not re-fire this — only at signup.)
            if (authMode === 'signup' && user?.id) {
                try {
                    await fetch('/api/me/profile', {
                        method:  'POST',
                        headers: {
                            'Content-Type':  'application/json',
                            'Authorization': 'Bearer ' + session.access_token,
                        },
                        body: JSON.stringify({ username }),
                    });
                } catch (e) {
                    console.warn('[auth] profile upsert after signup failed:', e);
                }
            }

            setBusinessDeadline();
            if (user?.email) localStorage.setItem(META_KEY, user.email);
            showAuthToast('✅ Signed in', 'Opening your dashboard…', 'success', 1500);
            setTimeout(goToDashboard, 350);
        } catch (err) {
            showAuthToast('⚠️ Sign-in failed', humaniseAuthError(err), 'error', 7000);
        } finally {
            submit.classList.remove('loading');
            submit.disabled = false;
        }
    });
}

function setAuthMode(next, refs) {
    authMode = next;
    refs.tabs.forEach(t => {
        const active = t.dataset.mode === next;
        t.classList.toggle('active', active);
        t.setAttribute('aria-selected', String(active));
    });
    // Toggle the User Name field's CSS visibility via [data-mode]
    // selector. Without this, the field stays hidden even when the
    // tab is "Sign Up".
    const authForm = document.getElementById('authForm');
    if (authForm) authForm.dataset.mode = next;
    if (refs.submitText) refs.submitText.textContent = next === 'signup' ? 'Create Account' : 'Sign In';
    const heading = document.querySelector('.auth-heading');
    const sub     = document.getElementById('authModeHint');
    if (heading) heading.textContent = next === 'signup' ? 'Create your account' : 'Welcome back';
    if (sub) sub.textContent = next === 'signup'
        ? '8-hour session, no password re-prompts.'
        : 'Sign in to continue to your screener & auto-buy desk.';
    if (refs.footline) {
        refs.footline.innerHTML = next === 'signup'
            ? 'Already have an account? <button type="button" class="auth-link-inline" id="authSwitchMode">Sign in</button>'
            : 'New here? <button type="button" class="auth-link-inline" id="authSwitchMode">Create an account</button>';
        const newSwitcher = document.getElementById('authSwitchMode');
        if (newSwitcher) {
            newSwitcher.addEventListener('click', e => {
                e.preventDefault();
                setAuthMode(authMode === 'signin' ? 'signup' : 'signin', { tabs: document.querySelectorAll('.auth-tab'), footline: document.getElementById('authFootline'), submitText: document.querySelector('.auth-submit-text') });
            });
        }
    }
}

function humaniseAuthError(err) {
    const raw = (err && (err.message || err.error_description)) || 'Unknown error';
    if (/Invalid login credentials/i.test(raw))         return 'Wrong email or password.';
    if (/User already registered/i.test(raw))           return 'That email is already in use — try Sign In instead.';
    if (/Password should be at least/i.test(raw))       return 'Password must be at least 6 characters.';
    if (/Email not confirmed/i.test(raw))               return 'Confirm your email first — check your inbox.';
    if (/rate limit/i.test(raw))                        return 'Too many attempts. Try again in a minute.';
    if (/Network|Failed to fetch/i.test(raw))           return 'Network error — check your connection.';
    return raw;
}

function goToDashboard() {
    // Same-origin sibling. Hard redirect — we want to clear any
    // auth-form state from the address bar.
    window.location.assign('/?authed=1');
}

function renderAuthFormDisabled() {
    const form  = document.getElementById('authForm');
    const tabs  = document.querySelectorAll('.auth-tab');
    const submit = document.getElementById('authSubmit');
    if (form)  form.style.opacity  = '0.55';
    if (submit) submit.disabled = true;
    tabs.forEach(t => t.setAttribute('disabled', 'true'));
}

/* =================================================================
 * TOAST (matches showToast in main shell)
 * =================================================================*/
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

/* ----------------------------------------------------------------
 * 5. KICK THE BOOT
 * ----------------------------------------------------------------*/
document.addEventListener('DOMContentLoaded', () => {
    initAuth().catch(err => {
        console.error('[auth] init failed:', err);
        showAuthToast('⚠️ Auth failed', 'Could not initialise — refresh to retry.', 'error');
    });
});
