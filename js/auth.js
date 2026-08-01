// ================================================================
// AUTHENTICATION — token management & API calls
// ================================================================

const AUTH_TOKEN_KEY = 'tapro_auth_token';
const AUTH_USER_KEY = 'tapro_auth_user';

// ── Token storage ───────────────────────────────────────────────

function getAuthToken() {
    return localStorage.getItem(AUTH_TOKEN_KEY);
}

function getAuthUser() {
    try {
        return JSON.parse(localStorage.getItem(AUTH_USER_KEY) || 'null');
    } catch { return null; }
}

function setAuth(token, user) {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
}

function clearAuth() {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
}

function isAuthenticated() {
    return !!getAuthToken();
}

// ── API calls ───────────────────────────────────────────────────

async function apiSignup(email, password, username) {
    const resp = await fetch('/api/auth/signup', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password, username }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Signup failed');
    return data;
}

async function apiSignin(email, password) {
    const resp = await fetch('/api/auth/signin', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Sign in failed');
    return data;
}

async function apiGetMe() {
    const token = getAuthToken();
    if (!token) throw new Error('Not authenticated');
    const resp = await fetch('/api/auth/me', {
        headers: { 'Authorization': `Bearer ${token}` },
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Session expired');
    return data;
}

// ── Auth header helper for other API calls ──────────────────────

function authHeaders() {
    const token = getAuthToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}

// ── Session management ──────────────────────────────────────────

async function checkAuth() {
    if (!isAuthenticated()) {
        // Not logged in — redirect to login page
        if (!window.location.pathname.includes('login.html')) {
            window.location.href = '/login.html';
        }
        return null;
    }

    try {
        const data = await apiGetMe();
        const user = data.user;
        // Update stored user info (in case it changed)
        const stored = getAuthUser();
        if (stored) setAuth(getAuthToken(), { ...stored, ...user });
        return user;
    } catch (e) {
        // Token expired or invalid — clear and redirect
        console.warn('[auth] Session invalid:', e.message);
        clearAuth();
        if (!window.location.pathname.includes('login.html')) {
            window.location.href = '/login.html';
        }
        return null;
    }
}

function logout() {
    clearAuth();
    window.location.href = '/login.html';
}
