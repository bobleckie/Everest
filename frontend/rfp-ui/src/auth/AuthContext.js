import React, { createContext, useContext, useEffect, useMemo, useState, useCallback } from 'react';
import axios from 'axios';

const AuthContext = createContext(null);

const TOKEN_KEY = 'rfp_token';
const USER_KEY = 'rfp_user';
const REFRESH_KEY = 'rfp_refresh_token';
const REDIRECT_KEY = 'rfp_post_login_redirect';

// Bare axios instance (no interceptors) for the refresh call itself.
// If we used the global axios here, a 401 on /api/auth/refresh would
// recurse forever.
const bareAxios = axios.create();

// Capture the current page so we can return the user here after they sign
// back in. Skip when we're already on /login or have no real path.
function capturePostLoginRedirect() {
  try {
    if (typeof window === 'undefined') return;
    const path = window.location.pathname || '/';
    const search = window.location.search || '';
    const hash = window.location.hash || '';
    if (path === '/login') return;
    const target = path + search + hash;
    if (target && target !== '/') {
      localStorage.setItem(REDIRECT_KEY, target);
    }
  } catch { /* localStorage may be unavailable */ }
}

// Install a single axios interceptor that adds Bearer token from localStorage.
// Doing it once at module load avoids fighting with React render cycles.
let interceptorInstalled = false;
let loginInProgress = false;
// Single in-flight refresh promise. All concurrent 401s wait on the same
// refresh call so we don't fire ten of them in parallel.
let refreshInFlight = null;

function tryRefreshOnce() {
  if (refreshInFlight) return refreshInFlight;
  const refresh = localStorage.getItem(REFRESH_KEY);
  if (!refresh) return Promise.resolve(null);
  refreshInFlight = bareAxios
    .post('/api/auth/refresh', { refresh_token: refresh })
    .then((res) => {
      const newAccess = res.data?.access_token;
      const newRefresh = res.data?.refresh_token;
      if (newAccess) localStorage.setItem(TOKEN_KEY, newAccess);
      if (newRefresh) localStorage.setItem(REFRESH_KEY, newRefresh);
      return newAccess || null;
    })
    .catch(() => null)
    .finally(() => { refreshInFlight = null; });
  return refreshInFlight;
}

function installAxiosInterceptor(getToken, onUnauthorized) {
  if (interceptorInstalled) return;
  interceptorInstalled = true;
  axios.interceptors.request.use((config) => {
    const t = getToken();
    if (t && !config.headers?.Authorization) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${t}`;
    }
    return config;
  });
  axios.interceptors.response.use(
    (r) => r,
    async (err) => {
      const status = err?.response?.status;
      const cfg = err?.config || {};
      const url = cfg.url || '';

      // Skip refresh logic entirely for dev-bypass + login attempts +
      // the refresh endpoint itself.
      const skipRefresh = (
        getToken() === 'dev-bypass-token' ||
        loginInProgress ||
        url.includes('/api/auth/token') ||
        url.includes('/api/auth/refresh') ||
        url.includes('/api/auth/redeem-invite') ||
        cfg._retriedAfterRefresh
      );

      if (status === 401 && !skipRefresh) {
        // Try one refresh, then replay the original request.
        const newAccess = await tryRefreshOnce();
        if (newAccess) {
          cfg._retriedAfterRefresh = true;
          cfg.headers = cfg.headers || {};
          cfg.headers.Authorization = `Bearer ${newAccess}`;
          return axios.request(cfg);
        }
        // Refresh failed AND the failing endpoint is the auth check itself
        // → real session loss. Otherwise, leave it as a regular 401 (the
        // page can decide what to do).
        if (url.includes('/api/auth/me') || url.includes('/api/auth/token')) {
          capturePostLoginRedirect();
          onUnauthorized({ expired: true });
        } else if (typeof console !== 'undefined') {
          console.warn('[auth] 401 on', url, '- refresh unavailable, NOT logging out.');
        }
      }
      return Promise.reject(err);
    }
  );
}

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => localStorage.getItem(TOKEN_KEY));
  const [user, setUser] = useState(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
  });
  // True only for the brief window between "auth endpoint returned 401"
  // and the user dismissing the toast / signing back in. Drives the global
  // session-expired Snackbar in App.js.
  const [sessionExpired, setSessionExpired] = useState(false);

  const logout = useCallback((opts) => {
    // Best-effort server-side revoke of the refresh token. Don't await —
    // we want logout to feel instant. The interceptor's refresh path
    // already handles the case where the access token is gone.
    try {
      const r = localStorage.getItem(REFRESH_KEY);
      if (r) {
        // Use bareAxios so a 401 on this call doesn't trigger refresh logic.
        bareAxios
          .post('/api/auth/logout', { refresh_token: r }, {
            headers: {
              Authorization: `Bearer ${localStorage.getItem(TOKEN_KEY) || ''}`,
            },
          })
          .catch(() => { /* best-effort; ignore failures */ });
      }
    } catch { /* localStorage may be unavailable */ }
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(REFRESH_KEY);
    setToken(null);
    setUser(null);
    if (opts && opts.expired) {
      setSessionExpired(true);
    }
  }, []);

  const dismissSessionExpired = useCallback(() => setSessionExpired(false), []);

  // Pop and return the saved post-login redirect target (one-shot).
  const consumePostLoginRedirect = useCallback(() => {
    try {
      const t = localStorage.getItem(REDIRECT_KEY);
      if (t) localStorage.removeItem(REDIRECT_KEY);
      return t;
    } catch { return null; }
  }, []);

  useEffect(() => {
    installAxiosInterceptor(() => localStorage.getItem(TOKEN_KEY), (opts) => {
      logout(opts);
    });
  }, [logout]);

  // On app boot, if we have a token in localStorage, validate it once via
  // /api/auth/me. If the JWT has expired the interceptor's onUnauthorized
  // path will log the user out, which routes them to the Login screen
  // instead of leaving them with an apparently-logged-in shell that
  // silently 401s every API call.
  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const me = await axios.get('/api/auth/me');
        if (cancelled) return;
        localStorage.setItem(USER_KEY, JSON.stringify(me.data));
        setUser(me.data);
      } catch {
        // The interceptor already triggered logout() if it was a 401 on
        // /api/auth/me. Other failures (network, 5xx) we leave alone so a
        // transient backend hiccup doesn't kick the user out.
      }
    })();
    return () => { cancelled = true; };
    // Run once on mount; re-runs on token change naturally cover login.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const refreshUser = useCallback(async () => {
    try {
      const me = await axios.get('/api/auth/me');
      localStorage.setItem(USER_KEY, JSON.stringify(me.data));
      setUser(me.data);
      return me.data;
    } catch (e) {
      return null;
    }
  }, []);

  const login = useCallback(async (username, password) => {
    // OAuth2 password flow: /api/auth/token expects form-encoded body
    loginInProgress = true;
    try {
    const body = new URLSearchParams();
    body.append('username', username);
    body.append('password', password);
    const res = await axios.post('/api/auth/token', body, {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    });
    const t = res.data.access_token;
    localStorage.setItem(TOKEN_KEY, t);
    // Persist the refresh token alongside (added in #8).
    if (res.data.refresh_token) {
      localStorage.setItem(REFRESH_KEY, res.data.refresh_token);
    }
    setToken(t);
    // Successful login clears any leftover "session expired" toast.
    setSessionExpired(false);
    // Fetch full profile so we know must_change_password, name, role
    let u = { username, must_change_password: !!res.data.must_change_password };
    localStorage.setItem(USER_KEY, JSON.stringify(u));
    setUser(u);
    const me = await (async () => {
      try {
        const r = await axios.get('/api/auth/me', { headers: { Authorization: `Bearer ${t}` } });
        return r.data;
      } catch { return null; }
    })();
    if (me) {
      localStorage.setItem(USER_KEY, JSON.stringify(me));
      setUser(me);
      u = me;
    }
    return u;
    } finally {
      loginInProgress = false;
    }
  }, []);

  // When app loads with an existing token, refresh the user profile once.
  useEffect(() => {
    if (token && (!user || user.must_change_password === undefined)) {
      refreshUser();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const value = useMemo(() => ({
    token,
    user,
    isAuthenticated: !!token,
    mustChangePassword: !!user?.must_change_password,
    sessionExpired,
    dismissSessionExpired,
    consumePostLoginRedirect,
    login,
    logout,
    refreshUser,
  }), [token, user, sessionExpired, dismissSessionExpired, consumePostLoginRedirect, login, logout, refreshUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
