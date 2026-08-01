/**
 * Auth helpers for TradeAlgo Pro.
 * Manages JWT token in localStorage and provides checkAuth / logout.
 */

const AUTH_TOKEN_KEY = 'tapro_token';
const AUTH_USER_KEY  = 'tapro_user';

/**
 * Save auth data after signup/signin.
 */
function saveAuth(token, user) {
    localStorage.setItem(AUTH_TOKEN_KEY, token);
    localStorage.setItem(AUTH_USER_KEY, JSON.stringify(user));
}

/**
 * Clear auth data (logout).
 */
function clearAuth() {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USER_KEY);
}

/**
 * Get the stored access token.
 */
function getToken() {
    return localStorage.getItem(AUTH_TOKEN_KEY);
}

/**
 * Get the stored user object.
 */
function getStoredUser() {
    try {
        const raw = localStorage.getItem(AUTH_USER_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch {
        return null;
    }
}

/**
 * Check if user is authenticated by verifying the stored token with the backend.
 * Returns the user object if valid, null otherwise.
 */
async function checkAuth() {
    const token = getToken();
    if (!token) return null;

    try {
        const resp = await fetch('/auth/me', {
            headers: { 'Authorization': `Bearer ${token}` },
        });
        const data = await resp.json();
        if (data.ok && data.user) {
            saveAuth(token, data.user);
            return data.user;
        }
    } catch (e) {
        console.warn('Auth check failed:', e);
    }

    // Token invalid — clean up
    clearAuth();
    return null;
}

/**
 * Logout: clear local state and redirect to login page.
 */
function logout() {
    clearAuth();
    window.location.href = '/login.html';
}

/**
 * Get auth headers for API calls.
 */
function authHeaders() {
    const token = getToken();
    return token ? { 'Authorization': `Bearer ${token}` } : {};
}
