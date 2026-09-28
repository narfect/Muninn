# Muninn — Production Modification Plan

> Author: analysis pass (code-review + security-audit + frontend + memory/LLM review).
> Status: **plan only — no code changed.** Hand this to Claude Code alongside
> `docs/CLAUDE_CODE_PROMPTS.md` to execute the build.
> Date: 2026-09-28.

## 0. Decisions this plan is built on

These were confirmed with the product owner before writing:

- **Architecture:** keep the **standard-library-only, offline-first** constraint. It is
  the project's whole "runs anywhere in one command" thesis and its hackathon
  eligibility. Auth and everything else is built with the Python stdlib (`hashlib`,
  `secrets`, `hmac`, `http.cookies`, `sqlite3`) and vanilla JS — no pip, no npm, no CDN.
- **Auth:** full accounts + roles — signup, login, hashed passwords, server-side
  sessions, and role-based access control with three roles: **admin / responder / viewer**.
- **Production scope:** hardening + polish (fix all bugs, robust error handling, expand
  tests, security, UX). Deployment/CI/observability are listed as *future* only.
- **Backends:** offline (`LocalMemoryStore` + `LocalReasoner`) and real
  (Hindsight + Groq) are both first-class and both get tests.

## 1. Current-state verdict

The repo is **not broken** — `compileall` is clean, all **58 tests pass**, and the server
boots and serves the UI and API. What look like "many errors" are a mix of: one real
learning-loop bug, a field-name contract mismatch that makes `root_cause` read as `null`
from the API, the complete absence of authentication, and a set of latent
robustness/accessibility gaps. None break the happy path; all matter for a
"production-grade" bar. Everything below is verified against actual file content with
line references.

## 2. The "root_cause is null" symptom (the thing you probably saw)

When you `POST /api/compare`, the brief's root cause comes back `null` for both cold and
warm even though citations work. **Cause: a field-name mismatch, not missing data.** The
`Brief` dataclass field is `root_cause_hypothesis` (`backend/models.py:134`) and
`Brief.as_dict()` returns `asdict(self)` (`backend/models.py:145-146`), so the JSON key is
literally `root_cause_hypothesis`. There is no `root_cause` key in the payload, so any
client reading `brief["root_cause"]` gets `null`. The value is actually always populated
(`backend/llm/client.py:226,238`, `backend/llm/agent.py:140`), and the bundled UI reads
the correct key (`static/app.js:269`) — only external clients hitting the raw JSON see the
null. Fix is a one-line compatibility alias (Phase 1).

## 3. Issues found (grouped by severity)

Severity key: **Blocker** = must fix for correctness/production; **Should-fix** =
real defect or contract gap; **Nit** = polish/consistency.

### 3.1 Blockers

- **B1 — `transition(status="resolved")` silently bypasses the learning loop.**
  `backend/services/incidents.py:80-94`. `transition()` accepts `resolved` and sets
  `resolved_at`, but does **not** compute `mttr_minutes` and does **not** call
  `memory.retain(...)`, unlike `resolve()` (`:96-123`). Both are live routes
  (`backend/api/routes.py:215,216`). Resolving via `/transition` produces an incident
  with `mttr_minutes=None` that is excluded from MTTR analytics
  (`backend/services/metrics.py:26-27`) and never retained — i.e. the mandatory,
  central Hindsight feature silently doesn't happen. **Fix:** have `transition()`
  delegate to a shared `_finalize_resolution` when target is `RESOLVED`, or reject
  `resolved` in `transition()` and force clients through `/resolve`.

- **B2 — Entire API is unauthenticated, including destructive endpoints.**
  `backend/api/routes.py:205-228`, `backend/server.py:133-139`. Anyone who can reach the
  port can `POST /api/demo/reset` and irreversibly wipe all data
  (`backend/db.py:279-283`), seed, create/resolve incidents, and (when keys are set) run
  up Groq/Hindsight spend via `POST /api/triage`. **Fix:** Phase 3 auth + RBAC.

### 3.2 Should-fix

- **S1 — `root_cause` contract mismatch** (see §2). `backend/models.py:134,145`. **Fix:**
  in `Brief.as_dict()`, after `asdict`, set `d["root_cause"] = d["root_cause_hypothesis"]`
  (keeps existing tests and UI green).
- **S2 — `remediation_steps` string is exploded into characters.**
  `backend/services/incidents.py:104`. A client sending `"remediation_steps":"rollback"`
  (a string) gets `["r","o","l",...]` stored and retained to memory. `create_incident`
  guards `tags` (`:62-63`) but `resolve` has no guard. **Fix:** validate
  `isinstance(remediation_steps, list)` and raise `ValueError` otherwise.
- **S3 — Collision-prone auto `external_id` → 500 on collision.**
  `backend/services/incidents.py:68` uses `f"INC-{now_ms() % 1_000_000}"`; the column is
  `UNIQUE NOT NULL` (`backend/db.py:37`) and `add_incident` doesn't catch
  `sqlite3.IntegrityError`. **Fix:** use a unique id + catch IntegrityError → `409`.
- **S4 — Not-found returns 400 instead of 404.** `transition/resolve/feedback` raise
  `ValueError("... not found")` → router maps to 400 (`backend/router.py:116-117`), while
  `GET /api/incidents/{id}` returns 404 (`backend/api/routes.py:61-62`). Inconsistent
  contract. **Fix:** add a `NotFoundError` the router maps to 404.

- **S5 — Hindsight bank is never created.** `HindsightStore.__init__`
  (`backend/memory/hindsight_store.py:31-42`) never calls `ensure_bank()`, and neither
  does `server.py`. On the real Hindsight path, `retain`/`recall` hit a bank that was
  never created (the local store is fine — it calls `ensure_bank()` in `__init__`).
  **Fix:** call `ensure_bank()` in `HindsightStore.__init__` or lazily in `retain`.
- **S6 — `HindsightClient._request` has no catch-all → can raise and break workflow.**
  `backend/memory/hindsight_client.py:57-83` catches HTTP/URL/JSON errors but has no final
  `except Exception`, and the store methods don't wrap the call. An unexpected error
  (e.g. `socket.timeout` on older Pythons, `UnicodeDecodeError`) propagates and hard-fails
  recall/reflect — violating the "never raise, degrade gracefully" contract
  (`backend/memory/base.py:22-24`). **Fix:** add a final `except Exception: return
  {"ok": False, ...}`.
- **S7 — Hindsight `reset()` missing → duplicate memories on re-seed.**
  `LocalMemoryStore.reset()` exists (`backend/memory/local_store.py:154-157`);
  `HindsightStore` has none, and both reset/seed guard with `hasattr(...,"reset")`, so on
  the Hindsight backend the bank is never cleared and re-seeding accumulates duplicates.
  **Fix:** implement `HindsightStore.reset()` or surface the skip in the UI.
- **S8 — `HindsightStore.count()` is process-local.** `backend/memory/hindsight_store.py:41,98-99`
  starts at 0 and only counts in-process retains; after restart it reports 0, corrupting
  `health.n_memories` and metrics. **Fix:** derive from a Hindsight stats/query call, or
  document as session-local.
- **S9 — Unbounded request body (memory-exhaustion DoS).** `backend/server.py:91-93`
  reads the full `Content-Length` with no cap. **Fix:** reject bodies over a configured
  max (e.g. 1 MB) with `413` before reading.
- **S10 — SSE framing/HEAD bugs.** `Response.sse` advertises `keep-alive` with no
  `Content-Length`/`Transfer-Encoding` (`backend/router.py:68-73`,
  `backend/server.py:107-117`), which under HTTP/1.1 can make browsers auto-reconnect and
  re-run triage; and `_write_stream` has no HEAD guard so `HEAD /api/triage/stream` spawns
  a worker and streams (`backend/server.py:104` vs `:107-117,147-148`). **Fix:** force
  `Connection: close` (or chunked) for SSE and short-circuit HEAD.
- **S11 — Citation → recall highlight never matches.** `static/app.js:294` highlights by
  `c.id` but recall rows are keyed by `m.source` (`:329,339-345`); clicking a citation
  highlights nothing unless the backend guarantees `citation.id === memory.source`.
  **Fix:** align the keys after confirming the `Brief`/`RecallResult` shapes.

### 3.3 Security hardening (in addition to B2)

- **SEC1 — No CSRF / Origin defense on state-changing requests.**
  `backend/server.py:133-139`. Once cookies exist, a drive-by page could
  `fetch(...reset..., {mode:'no-cors'})`. **Fix:** `SameSite=Strict` cookie + validate
  `Origin`/`Referer` on non-GET `/api` + a double-submit `X-CSRF-Token` header.
- **SEC2 — Exception text echoed to client.** `backend/router.py:114,117` return
  `endpoint not implemented yet: {exc}` / `bad request: {exc}` (the 500 path at `:118-120`
  is already generic and correct). **Fix:** generic messages for parse/OS errors; keep
  domain-validation messages; log detail server-side.
- **SEC3 — No response security headers.** `backend/server.py:95-105` sets no
  `X-Content-Type-Options: nosniff` etc. **Fix:** add basic hardening headers.
- **SEC4 — Open bind is one env var from exposure.** `MUNINN_HOST` default is `127.0.0.1`
  (`backend/config.py:58`); document that any non-loopback bind requires auth first.
- **SEC-note — No user-controlled SSRF or SQL injection** (verified). All SQL uses `?`
  placeholders; the only dynamic SQL (`db.py:283`) iterates a hardcoded table tuple.
  Outbound URLs come only from operator env config. Static path traversal is guarded
  (`backend/server.py:74-80`); residual symlink-follow is a minor note.

### 3.4 Nits (polish)

- Dead constants `_LIST_FIELDS`/`_JSON_FIELDS` (`backend/db.py:72-73`).
- `reset()` doesn't clear `sqlite_sequence` (`backend/db.py:279-283`) — IDs don't reset.
- Redundant double `init_db` (`backend/server.py:58-59`).
- `extract_entities` threshold (`>=5`) disagrees with its docstring (`>=4`)
  (`backend/memory/retrieval.py:156,162`).
- `top_k` float coerced to default instead of truncated (`backend/llm/tools.py:127`).
- `GroqClient` logs 4xx/5xx as "unreachable" and drops the body (`backend/llm/client.py:86`).
- Frontend a11y/polish: no `<h1>` (`static/index.html:13`); resolve-form inputs lack
  labels (`static/app.js:375-378`); `aria-selected` on `role="button"` (`:136-137,159`);
  canvas charts have no accessible text (`:438,443`); canvas not HiDPI-scaled;
  `window.prompt()` for new incidents (`:524-529`); dead SSE client code (`:32-48`).

## 4. Modification plan (phased)

Each phase is independently shippable and ends green. The matching copy-paste prompts
are in `docs/CLAUDE_CODE_PROMPTS.md`. Golden rules from `CLAUDE.md` hold throughout:
stdlib-only, offline-first, never fabricate, keep the suite green, verify before "done".

**Phase 0 — Baseline.** Read `CLAUDE.md` + this plan. Run `python -m compileall backend
tests`, `python -m unittest discover -s tests`, and a manual boot. Record the green
baseline. *Accept:* 58 tests pass, server boots.

**Phase 1 — Correctness fixes (no new features).** B1, S1–S4, S5–S8 (Hindsight parity),
plus the `root_cause` alias. Add a regression test per fix (esp. `transition→resolved`
retains + computes MTTR; string `remediation_steps` rejected; not-found → 404; duplicate
`external_id` → 409; `Brief.as_dict()` exposes `root_cause`). *Accept:* new tests added
and all pass; compileall clean.

**Phase 2 — Transport/security hardening (pre-auth).** S9 (body cap → 413), S10 (SSE
framing + HEAD guard), SEC2 (generic error text), SEC3 (security headers). *Accept:*
tests for body cap, HEAD on stream, and error-shape; suite green.

**Phase 3 — Authentication + RBAC.** The big one (design in §5). DB tables + repo methods,
`backend/services/auth.py` (hashing/sessions/lockout/policy/bootstrap), config secrets,
router middleware + role enforcement, `/api/auth/*` endpoints, admin user endpoints, CSRF.
*Accept:* full `tests/test_auth.py` (hashing round-trip, signup/login/logout, session
expiry, RBAC matrix incl. `reset` is admin-only, lockout, CSRF, schema idempotency) and
existing suite both green; manual dry-run of signup→login→protected call→logout.

**Phase 4 — Frontend auth + role-aware UI.** `static/auth.js` (`window.Auth`), auth screen
(login/signup tabs), boot gate via `GET /api/auth/me`, global 401 handling, CSRF header on
mutations, user chip + logout, admin users view, role-gated controls (§6). *Accept:*
manual walkthrough for each role; unauthenticated boot shows the gate and fetches no app
data.

**Phase 5 — Frontend polish + bug fixes.** S11 (citation↔recall keys), a11y nits (`<h1>`,
form labels, `aria`/listbox, canvas `role="img"`+data-table, HiDPI canvas), inline
new-incident form replacing `window.prompt`, loading/empty states, toast timing, and a
decision on SSE (wire real streaming or remove dead client code). *Accept:* manual a11y
pass; keyboard-only navigation works; no console errors.

**Phase 6 — Docs + final verification.** Update `README.md`, `CLAUDE.md`, `.env.example`
(new `MUNINN_SERVER_SECRET`, `MUNINN_COOKIE_SECURE`, session TTLs), `docs/TEST_PLAN.md`.
*Accept:* `compileall` + full suite green; full demo dry-run (seed as admin → cold/warm →
resolve & remember → insights) passes; README claims match reality.

## 5. Auth design (stdlib-only)

Role hierarchy: `viewer < responder < admin` (each inherits the levels below), enforced as
`RANK[user.role] >= RANK[required]`.

**Password hashing.** `hashlib.scrypt(pw, salt=salt, n=2**14, r=8, p=1, dklen=32,
maxmem=64*1024*1024)` with a `hashlib.pbkdf2_hmac("sha256", pw, salt, 600_000)` fallback
where scrypt `maxmem` is constrained. Per-user `secrets.token_bytes(16)` salt. Store an
upgradable string: `scrypt$16384$8$1$<b64 salt>$<b64 hash>`. Verify with
`hmac.compare_digest` (constant-time) — never `==`.

**Sessions.** Token = `secrets.token_urlsafe(32)`; store only `sha256(token)` in the DB so
a DB read can't resurrect live sessions. Cookie: `Set-Cookie: muninn_session=<token>;
HttpOnly; SameSite=Strict; Path=/; Max-Age=<ttl>` and add `Secure` when
`MUNINN_COOKIE_SECURE` is on (note: browsers won't send `Secure` cookies over
`http://127.0.0.1`, so it's a config flag — off for the local demo, on in production).
Absolute TTL (~12h) + idle timeout (~30 min, refresh `last_seen`). Logout deletes the row
and clears the cookie (`Max-Age=0`). Server-side sessions are preferred over signed cookies
because they're revocable and reflect role changes immediately.

**New tables** (add to `SCHEMA` in `backend/db.py`, all access via parameterized repo
methods `create_user`, `get_user_by_email`, `create_session`, `get_session`,
`touch_session`, `delete_session`, `count_users`):

```sql
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,           -- algo$params$salt$hash
    role TEXT NOT NULL DEFAULT 'viewer',   -- viewer|responder|admin
    created_at INTEGER NOT NULL,
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER                   -- epoch ms; NULL = not locked
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,           -- sha256(cookie token)
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
```

**Middleware.** Enforce in the single choke point `Router.dispatch` (`backend/router.py`)
or a thin wrapper in `Routes`. Static GETs bypass the router and stay public (the SPA
shell). Steps: read the `Cookie` header with `http.cookies.SimpleCookie` (use a
case-insensitive header lookup — `dict(self.headers)` at `server.py:138` drops
`http.client`'s case-insensitivity), hash the token, load + expiry-check the session, load
the user, attach `req.current_user`. Extend the route table tuples from
`(method, pattern, handler)` to `(method, pattern, handler, required_role)`:
unauthenticated → `401`, insufficient role → `403`, public routes skip the check. For
`GET /api/triage/stream` (SSE) the auth check must run **before** the stream starts and
return normal JSON 401/403, not an SSE frame.

**Endpoint → required role.**

| Method + path | Role |
|---|---|
| `GET /api/health` | public |
| `POST /api/auth/signup`, `POST /api/auth/login` | public |
| `POST /api/auth/logout`, `GET /api/auth/me` | authenticated |
| `GET /api/services`, `/api/runbooks`, `/api/incidents`, `/api/incidents/{id}` | viewer |
| `GET /api/metrics/*` | viewer |
| `POST /api/memory/recall` | viewer |
| `POST /api/incidents`, `/api/incidents/{id}/transition`, `/resolve`, `/feedback` | responder |
| `POST /api/triage`, `GET /api/triage/stream`, `POST /api/compare` | responder |
| `POST /api/memory/reflect` | responder |
| `POST /api/demo/seed`, `POST /api/demo/reset` | admin |
| `GET /api/users`, `PATCH /api/users/{id}` (role mgmt) | admin |

**New endpoints:** `POST /api/auth/signup`, `/login`, `/logout`, `GET /api/auth/me`, and
admin-only `GET /api/users` + `PATCH /api/users/{id}`.

**Anti-abuse.** Login lockout via `failed_attempts`/`locked_until` on the user row (after
~5 failures set `locked_until = now + 15m`, return `429`/`403`); optional per-IP counter
guarded by a `threading.Lock` (server is threaded). Password policy: min length (>=12
recommended, >=8 min), reject all-numeric and a small common-password denylist,
server-side. CSRF: `SameSite=Strict` + `Origin`/`Referer` validation + a double-submit
`X-CSRF-Token` header on mutations. **Bootstrap:** if `count_users()==0` the first signup
becomes `admin`; all later self-signups default to `viewer` (admin promotes). Uniform
auth errors + timing to prevent user enumeration.

## 6. Frontend auth + role-aware UI

Constraint: vanilla JS/CSS, no deps; reuse the existing design tokens
(`static/styles.css:4-32`) and the XSS-safe `el()`/`textContent` discipline
(`static/app.js:63`) — the frontend today has **zero** `innerHTML` usage; keep it that
way. Account identifier is **email** (matches the `users` schema); an optional display
`name` is shown in the user chip.

**Screens & flow.** On `DOMContentLoaded`, `Auth.require()` calls `GET /api/auth/me`.
`200` → hide the auth screen, boot the app, apply role gating; `401` → show the auth
screen and fetch no app data. Auth screen is a centered `.auth-card` on a full-viewport
backdrop with a `.toggle`-style Login⇆Sign-up tablist. Logged-in shell adds a user chip
(name + role badge) with a menu: Logout, and (admin only) a Users link. Logout →
`POST /api/auth/logout` → show auth screen. Admin `#/users` view: table of users with a
role `<select>`, gated in the router and hidden from non-admin nav.

**Client session handling.** Cookie is HttpOnly and same-origin, so `fetch`'s default
`credentials:"same-origin"` already sends it — do **not** add `credentials:"include"`.
`Auth.me()` bypasses the global 401 handler so a logged-out boot doesn't loop. Extend
`api._json` (`static/app.js:21-29`): on `401` (non-bootstrap) call `Auth.onUnauthorized()`
(clear user, show auth screen, toast "Session expired") before throwing. Send the
`X-CSRF-Token` header on every mutating fetch.

**Role capabilities (client hides; server still enforces).**

| Capability | viewer | responder | admin |
|---|:--:|:--:|:--:|
| View console, incidents, recall, insights; run triage/compare | ✅ | ✅ | ✅ |
| Create incident; resolve & retain; feedback | ❌ | ✅ | ✅ |
| Seed / reset demo data | ❌ | ❌ | ✅ |
| User management (`#/users`) | ❌ | ❌ | ✅ |

**New/edited files.** New `static/auth.js` (`window.Auth`: `me/login/signup/logout/
require/onUnauthorized/can/renderAuthScreen`), loaded before `app.js`. Edit
`static/index.html` (auth-screen markup, user chip, hidden users view, script tag),
`static/app.js` (wrap boot in `Auth.require()`, gate controls via `Auth.can()`, add
`#/users` route + 401 hook), and append to `static/styles.css` (`.auth-screen/.auth-card/
.auth-tabs/.form-row/label/.field-error/.user-chip/.role-badge/.users-table` + new
`--danger`, overlay, and container-width tokens).

**Auth-form a11y.** Explicit `<label for>` per input (no placeholder-as-label);
`autocomplete="email"`, `current-password` (login) / `new-password` (signup + confirm);
one `role="alert"` `aria-live="assertive"` error region per form; `aria-invalid` on bad
fields; focus first field on show, trap focus in the modal card, return focus to login on
logout; native `<form>` submit with a disabled "Signing in…" state.

## 7. Additional improvements & suggestions

Beyond fixes and auth. Grouped by whether they fit the agreed "hardening + polish" scope.

**In-scope (fold into Phases 1–6):**

- **Audit trail.** With auth in place, stamp who did what: add `actor` to the timeline on
  create/transition/resolve/feedback/seed/reset. High demo value, low cost.
- **Real SSE "thinking" stream.** The stream endpoint and client already exist but are
  dead (`static/app.js:32-48`). Wiring token-by-token triage makes the warm/cold demo feel
  alive; otherwise remove the dead code. Decide in Phase 5.
- **Incident search/filter + severity/service facets** in the queue — small, and makes a
  ~60-incident dataset usable.
- **Metrics caching.** `summary()` recomputes an O(n²) learning curve every call
  (`backend/services/metrics.py:79-100`); memoize per data version. Improves dashboard feel.
- **Empty/loading states** everywhere (queue has none today) and HiDPI-crisp charts.

**Future (out of the agreed scope — list, don't build now):**

- Deployment: `Dockerfile` + compose, `MUNINN_COOKIE_SECURE=1` behind TLS, healthcheck.
- CI: a GitHub Actions job running `compileall` + the unittest suite on push.
- Observability: structured JSON request logs, timing metrics, an error hook.
- Account lifecycle: password reset, email verification (offline-friendly stub), "log out
  all sessions", self-serve profile.
- Rate-limiting middleware across all endpoints (not just login).
- Export a triage brief / postmortem to Markdown or PDF.
- Pagination for `/api/incidents`; WebSocket live queue updates.

## 8. Testing strategy

Keep it offline-deterministic and network-free, per `CLAUDE.md`. Use a `tempfile.mkdtemp()`
sqlite path (not `:memory:`) because the code sets `PRAGMA journal_mode=WAL`
(`backend/db.py:81`) which can fail on sandboxed filesystems (the known sqlite gotcha).
Add regression tests with each fix (don't batch at the end): the resolved-via-transition
learning loop, string `remediation_steps`, 404 semantics, duplicate `external_id`,
`root_cause` alias, body-size cap, HEAD-on-stream, and the full `tests/test_auth.py`
(hashing, signup/login/logout, session expiry, RBAC matrix, lockout, CSRF). Preserve the
existing 58 green tests; turn the one remaining `@unittest.skip` stub into a real test as
its feature lands rather than deleting it.

## 9. Constraints & risks (reminders for the build)

- **Never relax the golden rules:** stdlib-only (no pip/npm/CDN), offline-first, never
  fabricate, keep the suite green, verify before claiming done, don't commit secrets.
- **Sandbox sqlite:** on the local demo, if `data/muninn.db` throws "disk I/O error", set
  `MUNINN_DB=/tmp/muninn.db`. On a normal machine the default is fine.
- **Cookie `Secure` over http://localhost** won't be sent by browsers — keep
  `MUNINN_COOKIE_SECURE` off for the local demo, on in production.
- **Don't change concrete contract signatures** (config/models/db/router/server) without
  reason; build against them. Do the work in phase order so each step stays shippable.

