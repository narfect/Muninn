# Muninn — Claude Code Prompt Pack

Copy-paste prompts to build the production version with **Claude Code**. Work through the
phases in order; each ends with the suite green and is a natural commit point. The full
rationale and line-referenced findings live in `docs/PRODUCTION_PLAN.md` — these prompts
assume Claude Code will read it.

## How to drive Claude Code (read once)

1. **Open the repo** (`claude` in the Muninn root) so `CLAUDE.md` is auto-loaded — it
   carries the hard constraints (stdlib-only, offline-first, keep tests green). Every
   prompt below also restates them so a compacted context can't drift.
2. **Use plan mode for the big phases** (especially Phase 3 auth). Press `Shift+Tab` to
   enter plan mode; Claude Code researches and proposes a plan without editing, you
   approve, then it executes. Great for multi-file changes.
3. **One phase per session where practical.** After a phase is committed, run `/clear` to
   reset context before the next phase so it stays sharp. Re-point it at
   `docs/PRODUCTION_PLAN.md` at the start of each.
4. **Make it verify, not claim.** Every prompt ends with an explicit acceptance test and
   "stop and report." Claude Code should run `python -m compileall backend tests` and
   `python -m unittest discover -s tests` and paste results before saying done.
5. **Let it parallelize analysis.** For read-heavy steps ("find every caller of X"),
   tell it to use subagents / the Task tool.
6. **Commit per phase** with a clear message; don't force-push; don't commit `.env`.
7. **Sandbox note:** if sqlite throws "disk I/O error", run with `MUNINN_DB=/tmp/muninn.db`.

Paste this **preamble** at the top of each phase prompt (or rely on `CLAUDE.md`):

```
You are working in the Muninn repo. Hard rules (from CLAUDE.md): Python standard library
ONLY (no pip), vanilla JS/CSS for the frontend (no npm/CDN), offline-first, never fabricate
data, keep `python -m unittest discover -s tests` green, and verify (compileall + tests)
before claiming done. Read docs/PRODUCTION_PLAN.md first. Do not change concrete contract
signatures (config/models/db/router/server) without saying why. Work only on this phase;
stop and report when the acceptance test passes.
```

## Phase 0 — Baseline

```
Read CLAUDE.md and docs/PRODUCTION_PLAN.md. Then establish a green baseline WITHOUT
changing any code:
- Run: python -m compileall backend tests
- Run: python -m unittest discover -s tests -v
- Start the server (use MUNINN_DB=/tmp/muninn.db if the default path errors), curl
  GET /api/health, POST /api/demo/seed, then POST /api/compare with a sample SEV1 alert,
  and confirm warm has higher confidence + citations than cold.
Report the test count, pass/fail, and the compare output. Do not fix anything yet —
just confirm the starting state and list which phase you'd do next.
```

Acceptance: 58 tests pass, server boots, compare shows warm > cold. Stop and report.

## Phase 1 — Correctness fixes

```
[preamble]
Goal: fix the correctness bugs in docs/PRODUCTION_PLAN.md §3.1–§3.2 with a regression
test for each. Do NOT add auth or UI here.

Fix these, each with a new/updated test in tests/ that fails before and passes after:
1. B1 — backend/services/incidents.py transition(): when target status is "resolved",
   it must compute mttr_minutes AND retain to memory exactly like resolve(). Refactor a
   shared private _finalize_resolution() used by both, OR reject "resolved" in
   transition() and require /resolve. Test that resolving via /transition retains a
   memory and sets mttr_minutes.
2. S1 (root_cause alias) — in backend/models.py Brief.as_dict(), after asdict, add
   d["root_cause"] = d["root_cause_hypothesis"]. Test that /api/compare JSON has a
   non-null root_cause for warm.
3. S2 — resolve(): reject a non-list remediation_steps with ValueError (mirror the tags
   guard at incidents.py:62-63). Test with a string body.
4. S3 — collision-proof external_id generation and catch sqlite3.IntegrityError in
   db.add_incident to return a clean 409 (add a NotFoundError/ConflictError if helpful).
5. S4 — not-found on transition/resolve/feedback must return 404, not 400. Introduce a
   NotFoundError that router.py maps to 404; keep ValueError->400 for real bad input.
6. S5–S8 (Hindsight parity) — call ensure_bank() in HindsightStore.__init__; add
   HindsightStore.reset(); add a final `except Exception` in HindsightClient._request
   returning {"ok": False, ...}; and either derive count() from the service or document
   it as session-local. These need offline-safe tests (mock/skip network).

Then: python -m compileall backend tests && python -m unittest discover -s tests -v.
Report a summary of each fix, the new tests, and the final pass count.
```

Acceptance: every existing test still passes + new regression tests pass; compileall clean.

## Phase 2 — Transport & security hardening (pre-auth)

```
[preamble]
Goal: harden the HTTP layer before adding auth (docs/PRODUCTION_PLAN.md §3.3, S9–S10).
No auth yet.

1. S9 — backend/server.py _read_body(): reject requests whose Content-Length exceeds a
   configurable MAX_BODY_BYTES (default 1 MB) with HTTP 413 before reading the body.
2. S10 — SSE: for streaming responses force `Connection: close` and set
   self.close_connection = True after streaming, and short-circuit HEAD in _write_stream
   (a HEAD to /api/triage/stream must NOT spawn a worker or stream a body).
3. SEC2 — router.py: return generic messages for parse/OS errors (keep domain-validation
   ValueError messages), and never echo exception text for NotImplementedError.
4. SEC3 — add security headers (X-Content-Type-Options: nosniff, and a minimal set) on
   responses in server.py _write().

Add tests: oversized body -> 413; HEAD /api/triage/stream -> no body/no worker; error
responses don't leak internals. Then compileall + unittest. Report results.
```

Acceptance: new hardening tests pass; full suite green.

## Phase 3 — Authentication + RBAC (use plan mode)

```
[preamble]
This is the largest phase — ENTER PLAN MODE first (Shift+Tab), propose a plan against
docs/PRODUCTION_PLAN.md §5, let me approve, then implement. Constraint: stdlib only
(hashlib, secrets, hmac, http.cookies, sqlite3) — no external auth libraries.

Build full accounts + roles (admin/responder/viewer), exactly as specified in
docs/PRODUCTION_PLAN.md §5:
1. DB (backend/db.py): add the users and sessions tables from §5 to SCHEMA (idempotent)
   plus parameterized Repository methods: create_user, get_user_by_email, count_users,
   create_session, get_session, touch_session, delete_session.
2. backend/services/auth.py (new): scrypt password hashing (pbkdf2_hmac fallback) with
   per-user salt and hmac.compare_digest; session create/verify/expiry (store sha256 of
   the token); login lockout (failed_attempts/locked_until); password policy;
   first-user-becomes-admin bootstrap.
3. backend/config.py: add MUNINN_SERVER_SECRET, session TTLs, and MUNINN_COOKIE_SECURE
   (default off for local http).
4. Auth middleware: parse the Cookie header (case-insensitive; http.cookies.SimpleCookie),
   attach req.current_user in router.py, and extend the route table to
   (method, pattern, handler, required_role); enforce 401 (unauth) / 403 (insufficient).
   Static GETs stay public. For the SSE route, do the auth check before streaming and
   return JSON 401/403, not an SSE frame.
5. Endpoints (backend/api/routes.py): POST /api/auth/signup, /login, /logout,
   GET /api/auth/me, plus admin-only GET /api/users and PATCH /api/users/{id}. Apply the
   §5 endpoint→role table (demo/seed + demo/reset become admin-only; mutations
   responder+; reads viewer+).
6. CSRF: SameSite=Strict cookie + validate Origin/Referer on non-GET /api + require an
   X-CSRF-Token double-submit header.

Write tests/test_auth.py (offline, tempfile sqlite path — not :memory:, because of WAL):
hashing round-trip + wrong password + compare_digest usage; signup (first=admin,
second=viewer, duplicate email rejected, weak password rejected); login sets HttpOnly
SameSite cookie; wrong creds 401; lockout after N -> 429/403; session valid/expired/idle;
logout invalidates; RBAC matrix (unauth 401, viewer 403 on responder/admin routes,
responder 403 on admin, admin all-ok; assert POST /api/demo/reset is admin-only); CSRF
rejects foreign Origin / missing token; schema idempotent.

Verify: compileall + full suite (existing 58 + new) green. Then manual dry-run: signup
(becomes admin), login, call a protected route with the cookie, hit an admin-only route
as a viewer -> 403, logout -> protected route 401. Report the RBAC matrix results.
```

Acceptance: tests/test_auth.py + existing suite green; manual RBAC dry-run matches §5.

## Phase 4 — Frontend auth + role-aware UI

```
[preamble]
Goal: add the login/signup experience and role-aware UI on the frontend, matching
docs/PRODUCTION_PLAN.md §6. Constraint: vanilla JS/CSS, no deps, no CDN. Reuse the design
tokens in static/styles.css:4-32 and keep the frontend's zero-innerHTML discipline — build
DOM with the existing el()/textContent helper (static/app.js:63). Account identifier is
EMAIL; an optional display name shows in the user chip.

1. New static/auth.js exposing window.Auth with: me(), login(), signup(), logout(),
   require(), onUnauthorized(), can(capability), renderAuthScreen(). Load it BEFORE app.js
   via a <script> tag in index.html.
2. Boot gate: on DOMContentLoaded, Auth.require() calls GET /api/auth/me. 200 -> hide auth
   screen, boot the app, apply role gating. 401 -> show the auth screen and fetch NO app
   data. Auth.me() must bypass the global 401 handler so a logged-out boot doesn't loop.
3. Auth screen: centered .auth-card on a full-viewport backdrop, Login⇆Sign-up tablist.
   Native <form> submit with a disabled "Signing in…" state.
4. Session/CSRF on the client: the cookie is HttpOnly + same-origin, so fetch's default
   credentials:"same-origin" already sends it — do NOT add credentials:"include". Extend
   api._json (static/app.js:21-29): on a non-bootstrap 401, call Auth.onUnauthorized()
   (clear user, show auth screen, toast "Session expired") before throwing. Send an
   X-CSRF-Token header on every mutating fetch.
5. Logged-in shell: a user chip (name + role badge) with a menu — Logout, and (admin only)
   a Users link. Logout -> POST /api/auth/logout -> show auth screen.
6. Role gating (client hides; server still enforces — see §6 capability table): viewer can
   view/triage/compare only; responder adds create/resolve/feedback; admin adds seed/reset
   and the #/users view. Gate controls via Auth.can(); hide the seed/reset buttons and the
   Users nav from non-admins. Add the admin #/users view: a users table with a role <select>
   (calls PATCH /api/users/{id}), gated in the router and hidden from non-admin nav.
7. Edit static/index.html (auth-screen markup, user chip, hidden users view, the auth.js
   script tag) and append to static/styles.css (.auth-screen/.auth-card/.auth-tabs/
   .form-row/label/.field-error/.user-chip/.role-badge/.users-table + a --danger token,
   overlay, and container-width tokens).
8. Auth-form a11y: explicit <label for> per input (no placeholder-as-label);
   autocomplete="email", current-password (login) / new-password (signup + confirm); one
   role="alert" aria-live="assertive" error region per form; aria-invalid on bad fields;
   focus first field on show, trap focus in the modal card, return focus on logout.

This phase is UI + wiring only — do not change backend contracts from Phase 3. There are
no unit tests for static assets, so VERIFY manually: start the server, and for each role
(admin, responder, viewer) walk through login -> the gated controls appear/disappear
correctly -> logout. Confirm an unauthenticated boot shows the gate and issues no app-data
requests (check the network tab / server logs). Then compileall + unittest to confirm the
backend suite is still green. Report the per-role walkthrough results.
```

Acceptance: per-role manual walkthrough passes; unauthenticated boot shows the gate and
fetches no app data; backend suite still green.

## Phase 5 — Frontend polish + bug fixes

```
[preamble]
Goal: fix the remaining frontend bug and finish the polish/a11y items in
docs/PRODUCTION_PLAN.md §3.4 and §4 (Phase 5). Still vanilla JS/CSS, zero innerHTML.

1. S11 — citation↔recall key mismatch: reconcile static/app.js:294 with the keys the
   recall payload actually uses (static/app.js:329, 339-345) so citations render against
   the right incidents. Read the compare/recall JSON shape first, then fix the accessor.
2. a11y: add a real page <h1>; associate every form control with a <label>; add
   aria/listbox semantics to the alert picker; give the charts role="img" with an
   aria-label AND an offscreen data-table fallback; render canvases at devicePixelRatio
   for HiDPI crispness.
3. Replace the window.prompt new-incident flow with an inline, labeled <form> (same a11y
   rules as the auth form).
4. Add loading and empty states to the incident queue (it has none today) and sane toast
   timing.
5. SSE decision: the stream endpoint + client exist but are dead (static/app.js:32-48).
   Either wire real token-by-token triage streaming end-to-end, or remove the dead client
   code and the unused route. Pick one, state which and why, and leave no dead code.
6. (In-scope extras from §7, fold in if cheap:) audit-trail actor already stamped by the
   backend — surface it in the timeline UI; add incident search/filter + severity/service
   facets to the queue.

No new backend unit tests are required here unless you touch backend code (if you wire
SSE, add a HEAD/stream test). VERIFY manually: keyboard-only navigation works end to end;
a screen-reader/aria pass on forms and charts; no console errors; the cold⇄warm demo still
renders citations correctly. Then compileall + unittest (backend still green). Report the
a11y pass and the SSE decision.
```

Acceptance: manual a11y + keyboard pass; citations render correctly; no console errors;
backend suite green.

## Phase 6 — Docs + final verification

```
[preamble]
Goal: bring the docs in line with the shipped app and run the full end-to-end verification
(docs/PRODUCTION_PLAN.md §4, Phase 6). No new features.

1. Update README.md so its claims match reality: auth now exists (accounts + roles), the
   quick-start notes first-user-becomes-admin, and the project-status section reflects that
   the feature build is done (no longer a "scaffold"). Do NOT overstate — keep the
   synthetic-data labeling and the offline-first framing.
2. Update CLAUDE.md if any contract changed (new auth service, route-table shape, config
   keys).
3. Update .env.example with the new keys: MUNINN_SERVER_SECRET (document that it must be
   set to a strong random value in any non-local deployment), MUNINN_COOKIE_SECURE, and the
   session TTLs. Never commit a real secret.
4. Update docs/TEST_PLAN.md to list the new test files/cases (auth, hardening, regressions)
   and the final test count.
5. Final verification: python -m compileall backend tests, then
   python -m unittest discover -s tests -v (all green). Then a full manual demo dry-run:
   sign up (becomes admin) -> seed demo data -> pick a SEV1 alert -> flip cold⇄warm (warm
   shows higher confidence + citations) -> resolve & remember (counter ticks up) -> open
   Insights (MTTR + learning curve render). Confirm a viewer cannot seed/reset and a
   logged-out user hits the gate.

Report: final test count + pass/fail, the demo dry-run result step by step, and a short
list of anything deferred to the "future / out-of-scope" bucket in §7.
```

Acceptance: compileall + full suite green; full demo dry-run passes; README/CLAUDE.md/
.env.example/TEST_PLAN.md match the shipped app.

## After all phases

You now have a hardened, authenticated, role-aware Muninn with the learning loop fixed and
the frontend polished — all under the original constraints (stdlib-only, offline-first, no
fabricated data, green suite). If you want to go further, pick from the "Future" list in
`docs/PRODUCTION_PLAN.md §7` (Docker + CI, observability, account lifecycle, brief export,
pagination) — each is a clean, self-contained follow-up phase in the same
prompt-then-verify style used above.
