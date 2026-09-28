/* Muninn frontend auth — vanilla JS, no deps, no CDN, zero innerHTML.
   Owns the login/sign-up experience, the session boot gate (GET /api/auth/me),
   role gating (Auth.can), and the CSRF token echoed on mutating requests.
   Loaded BEFORE app.js; exposes window.Auth. Wrapped in an IIFE so it can keep
   its own el()/$ helpers without colliding with app.js's globals. */

"use strict";

(function () {
  const SESSION_COOKIE = "muninn_session";  // HttpOnly — invisible to JS (server-only)
  const CSRF_COOKIE = "muninn_csrf";         // readable — echoed as X-CSRF-Token

  // Role hierarchy (mirrors backend/models.py ROLE_RANK) + client capability gates.
  // Capabilities mirror the SERVER route table (docs §5), so the UI never offers a
  // control the server would 403: the client hides, the server still enforces.
  const RANK = { viewer: 0, responder: 1, admin: 2 };
  const CAP_MIN = {
    view: "viewer", recall: "viewer",
    triage: "responder", mutate: "responder",
    seed: "admin", reset: "admin", users: "admin",
  };

  let _user = null;      // the authenticated account, or null when logged out
  let _csrf = "";        // cached CSRF token (cookie is authoritative — see csrf())
  let _onAuth = null;    // app boot callback, registered by app.js via onAuthenticated()
  let _lastFocus = null; // element focused before the auth screen opened

  /* ---- tiny DOM helpers (local to this module) ---- */
  const qs = (sel, root) => (root || document).querySelector(sel);
  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };
  const clearNode = (n) => { while (n.firstChild) n.removeChild(n.firstChild); };
  function readCookie(name) {
    const parts = document.cookie ? document.cookie.split("; ") : [];
    for (const p of parts) {
      const i = p.indexOf("=");
      if (i > -1 && p.slice(0, i) === name) return decodeURIComponent(p.slice(i + 1));
    }
    return "";
  }

  /* ---- auth-owned fetch (bypasses app.js's global 401 handler) ----
     me()/login()/signup()/logout() go through here so a failed login (401) shows a
     form error rather than tripping the "session expired" boot loop in app.js. */
  async function authFetch(path, opts) {
    const o = opts || {};
    const headers = Object.assign({ "Accept": "application/json" }, o.headers || {});
    const mutating = o.method && o.method !== "GET";
    if (o.body != null) headers["Content-Type"] = "application/json";
    if (mutating) { const c = Auth.csrf(); if (c) headers["X-CSRF-Token"] = c; }
    const r = await fetch(path, {
      method: o.method || "GET", headers,
      body: o.body != null ? JSON.stringify(o.body) : undefined,
    });
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty / non-JSON body */ }
    if (!r.ok) {
      const msg = (data && (data.message || data.error)) || `HTTP ${r.status}`;
      const err = new Error(msg); err.status = r.status; err.data = data; throw err;
    }
    return data;
  }

  function _setUser(user, csrf) {
    _user = user || null;
    if (csrf) _csrf = csrf;
  }

  /* ---- public API surface (window.Auth) ---- */
  const Auth = {
    user() { return _user; },
    // The muninn_csrf cookie is JS-readable and always current; fall back to cache.
    csrf() { return readCookie(CSRF_COOKIE) || _csrf; },
    can(cap) {
      if (!_user) return false;
      const need = CAP_MIN[cap];
      if (need == null) return false;
      const have = RANK[_user.role];
      return have != null && have >= RANK[need];
    },
    onAuthenticated(cb) { _onAuth = cb; },

    async me() {
      const data = await authFetch("/api/auth/me");   // raw — bypasses global 401 handler
      _setUser(data.user, data.csrf);
      return data.user;
    },

    async login(email, password) {
      const data = await authFetch("/api/auth/login", { method: "POST", body: { email, password } });
      _setUser(data.user, readCookie(CSRF_COOKIE));
      return data.user;
    },

    async signup(email, password, name) {
      const data = await authFetch("/api/auth/signup",
        { method: "POST", body: { email, password, name: name || "" } });
      _setUser(data.user, readCookie(CSRF_COOKIE));
      return data.user;
    },

    async logout() {
      try { await authFetch("/api/auth/logout", { method: "POST" }); }
      catch (_) { /* drop the client session regardless of the server's reply */ }
      _user = null; _csrf = "";
      _showAuth("You've been signed out.");
    },

    // Boot gate: 200 -> hide gate, boot the app, apply role gating; 401 -> show the
    // gate and fetch NO app data (me() bypasses the global 401 handler, so no loop).
    async require() {
      try {
        const user = await Auth.me();
        _showApp();
        if (typeof _onAuth === "function") _onAuth(user);
        return user;
      } catch (_) {
        _showAuth();
        return null;
      }
    },

    onUnauthorized(msg) {
      _user = null; _csrf = "";
      _showAuth(msg || "Session expired — please sign in again.");
    },

    renderAuthScreen(message) { _renderAuthScreen(message); },
  };
  window.Auth = Auth;

  /* ---- screen show/hide ---- */
  function _showApp() {
    const root = qs("#auth-root");
    if (root) { root.hidden = true; root.setAttribute("aria-hidden", "true"); clearNode(root); }
    const app = qs("#app");
    if (app) app.hidden = false;
    document.removeEventListener("keydown", _trapFocus, true);
    if (_lastFocus && typeof _lastFocus.focus === "function") { try { _lastFocus.focus(); } catch (_) {} }
  }

  function _showAuth(message) {
    const app = qs("#app");
    if (app) app.hidden = true;
    _renderAuthScreen(message);
  }

  function _finishAuth(user) {
    _showApp();
    if (typeof _onAuth === "function") _onAuth(user);
  }

  /* ---- form building blocks (labelled, accessible) ---- */
  function _formRow(id, labelText, type, autocomplete, required) {
    const row = el("div", "form-row");
    const label = el("label", null, labelText);
    label.htmlFor = id;
    const input = el("input", "field");
    input.id = id; input.name = id; input.type = type;
    if (autocomplete) input.autocomplete = autocomplete;
    if (required) input.required = true;
    row.appendChild(label); row.appendChild(input);
    return { row, input };
  }
  function _errorRegion(id) {
    const e = el("p", "field-error");
    e.id = id;
    e.setAttribute("role", "alert");
    e.setAttribute("aria-live", "assertive");
    e.hidden = true;
    return e;
  }
  function _setError(region, inputs, msg) {
    region.textContent = msg; region.hidden = false;
    (inputs || []).forEach((i) => i.setAttribute("aria-invalid", "true"));
  }
  function _clearError(region, inputs) {
    region.textContent = ""; region.hidden = true;
    (inputs || []).forEach((i) => i.removeAttribute("aria-invalid"));
  }
  function _busy(btn, on, label) { btn.disabled = on; btn.textContent = label; }

  function _buildLoginForm() {
    const form = el("form", "auth-form"); form.id = "login-form"; form.noValidate = true;
    form.setAttribute("role", "tabpanel");
    form.setAttribute("aria-labelledby", "tab-login");
    const err = _errorRegion("login-error");
    const email = _formRow("login-email", "Email", "email", "email", true);
    const pw = _formRow("login-password", "Password", "password", "current-password", true);
    const submit = el("button", "btn auth-submit", "Sign in"); submit.type = "submit";
    [err, email.row, pw.row, submit].forEach((n) => form.appendChild(n));
    const setError = (m) => _setError(err, [email.input, pw.input], m);
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      _clearError(err, [email.input, pw.input]);
      if (!email.input.value.trim() || !pw.input.value) { setError("Enter your email and password."); return; }
      _busy(submit, true, "Signing in…");
      try {
        const user = await Auth.login(email.input.value.trim(), pw.input.value);
        _finishAuth(user);
      } catch (ex) {
        setError(ex.status === 429 ? "Too many attempts — try again in a few minutes."
                                   : (ex.message || "Sign in failed."));
        _busy(submit, false, "Sign in");
        pw.input.focus();
      }
    });
    return { form, first: email.input, setError };
  }

  function _buildSignupForm() {
    const form = el("form", "auth-form"); form.id = "signup-form"; form.hidden = true;
    form.noValidate = true;
    form.setAttribute("role", "tabpanel");
    form.setAttribute("aria-labelledby", "tab-signup");
    const err = _errorRegion("signup-error");
    const name = _formRow("signup-name", "Display name (optional)", "text", "name", false);
    const email = _formRow("signup-email", "Email", "email", "email", true);
    const pw = _formRow("signup-password", "Password", "password", "new-password", true);
    const confirm = _formRow("signup-confirm", "Confirm password", "password", "new-password", true);
    const hint = el("p", "auth-hint", "At least 8 characters. The first account created becomes the admin.");
    const submit = el("button", "btn auth-submit", "Create account"); submit.type = "submit";
    [err, name.row, email.row, pw.row, confirm.row, hint, submit].forEach((n) => form.appendChild(n));
    const inputs = [name.input, email.input, pw.input, confirm.input];
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      _clearError(err, inputs);
      const emailV = email.input.value.trim();
      if (!emailV) { _setError(err, [email.input], "Email is required."); return; }
      if (pw.input.value.length < 8) { _setError(err, [pw.input], "Password must be at least 8 characters."); return; }
      if (pw.input.value !== confirm.input.value) {
        _setError(err, [pw.input, confirm.input], "Passwords don't match."); return;
      }
      _busy(submit, true, "Creating…");
      try {
        const user = await Auth.signup(emailV, pw.input.value, name.input.value.trim());
        _finishAuth(user);
      } catch (ex) {
        _setError(err, [email.input], ex.message || "Sign up failed.");
        _busy(submit, false, "Create account");
      }
    });
    return { form, first: name.input };
  }

  /* ---- the auth screen (centered card on a full-viewport backdrop) ---- */
  function _renderAuthScreen(message) {
    _lastFocus = document.activeElement;
    const root = qs("#auth-root");
    if (!root) return;
    clearNode(root);
    root.hidden = false; root.removeAttribute("aria-hidden");

    const card = el("div", "auth-card");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "auth-title");

    const brand = el("div", "auth-brand");
    brand.appendChild(el("span", "rune", "ᛗ"));
    const title = el("h1", "auth-title", "muninn"); title.id = "auth-title";
    brand.appendChild(title);
    card.appendChild(brand);
    card.appendChild(el("p", "auth-tagline", "Incident memory — sign in to continue."));

    const tabs = el("div", "auth-tabs");
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "Log in or create an account");
    const loginTab = el("button", "auth-tab", "Log in"); loginTab.id = "tab-login";
    const signupTab = el("button", "auth-tab", "Sign up"); signupTab.id = "tab-signup";
    [loginTab, signupTab].forEach((t) => {
      t.type = "button"; t.setAttribute("role", "tab");
    });
    loginTab.setAttribute("aria-controls", "login-form");
    signupTab.setAttribute("aria-controls", "signup-form");
    tabs.appendChild(loginTab); tabs.appendChild(signupTab);
    card.appendChild(tabs);

    const login = _buildLoginForm();
    const signup = _buildSignupForm();
    card.appendChild(login.form); card.appendChild(signup.form);
    root.appendChild(card);

    const select = (which) => {
      const isLogin = which === "login";
      loginTab.setAttribute("aria-selected", String(isLogin));
      signupTab.setAttribute("aria-selected", String(!isLogin));
      loginTab.tabIndex = isLogin ? 0 : -1;
      signupTab.tabIndex = isLogin ? -1 : 0;
      login.form.hidden = !isLogin;
      signup.form.hidden = isLogin;
      const first = (isLogin ? login : signup).first;
      if (first) first.focus();
    };
    loginTab.addEventListener("click", () => select("login"));
    signupTab.addEventListener("click", () => select("signup"));
    tabs.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
      e.preventDefault();
      select(document.activeElement === loginTab ? "signup" : "login");
    });

    select("login");
    if (message) login.setError(message);   // keep focus on the field, announce the message
    document.addEventListener("keydown", _trapFocus, true);
  }

  /* ---- focus trap: keep Tab inside the modal card while the gate is up ---- */
  function _trapFocus(e) {
    if (e.key !== "Tab") return;
    const root = qs("#auth-root");
    if (!root || root.hidden) return;
    const nodes = root.querySelectorAll(
      'a[href], button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])');
    const visible = Array.prototype.filter.call(nodes, (n) => n.offsetParent !== null);
    if (!visible.length) return;
    const first = visible[0], last = visible[visible.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
})();
