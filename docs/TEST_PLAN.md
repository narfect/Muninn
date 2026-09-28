# Muninn — Test Plan

Status: **complete and green**. The full suite in `tests/` runs with
`python -m unittest discover -s tests -v` and reports **119 tests OK** — no skips
remain. Every `@unittest.skip("IMPLEMENT ...")` stub from the scaffold has been turned
into a real test as its feature landed, and the auth/RBAC, transport-hardening, and
correctness-regression layers added their own files. The count and pass/fail are the
exit gate for the feature build.

## Principles
- **Standard library only** (`unittest`) — no pytest, no network, no installs.
- **Deterministic & offline**: exercise the `local` memory + `local` reasoner backends
  so the suite never depends on Hindsight/Groq being reachable. Real-backend paths get
  thin, network-gated integration checks (skipped unless creds+egress present).
- **Fast**: use a temp SQLite file per test (`dataclasses.replace(settings, db_path=…)`).
- **Never fabricate a pass**: skipped tests are visible TODOs, not hidden gaps.

## How to run
```
python -m unittest discover -s tests -v        # full suite
python -m compileall backend tests             # import/syntax gate
```

## Coverage matrix (FR → test file → status)
All rows are **green** — the suite has no skipped tests. Counts below sum to **119**.

| FR / area | test file | tests | status |
|---|---|---|---|
| Domain models, signature_text, as_dict | test_models.py | 5 | green |
| Config backend resolution (auto/forced) | test_config.py | 4 | green |
| Router: path params, 404, 501, JSON errors | test_router.py | 8 | green |
| Dataset integrity (schema, recurrence) | test_dataset.py | 6 | green |
| FR-2/3 recall + fusion scoring | test_retrieval.py | 6 | green |
| FR-5 retain / round-trip (LocalMemoryStore) | test_memory_local.py | 6 | green |
| Hindsight store parity (client + store) | test_hindsight_store.py | 6 | green |
| FR-1/4/6/7/14-16 lifecycle · learning · metrics | test_services.py | 9 | green |
| FR-11/12 agent tool-loop + malformed-call robustness | test_agent.py | 6 | green |
| FR-8/9/13 compare · feedback · SSE + API happy-path | test_api_endpoints.py | 9 | green |
| Correctness regressions (B1, S1–S4) | test_correctness.py | 11 | green |
| Auth: hashing · policy · signup · login/lockout · sessions · CSRF token · roles · cookies · RBAC-through-router · auth endpoints · CSRF middleware | test_auth.py | 34 | green |
| Transport hardening (S9 body cap · S10 SSE HEAD guard · SEC2 error-text · SEC3 headers) | test_hardening.py | 9 | green |

## New test files added during the feature build
- **test_auth.py** (34) — password hashing (salted, recognized algo, safe on bad
  stored strings), password policy, first-user-becomes-admin signup, duplicate-email
  conflict, login success + wrong-creds `AuthError`, failed-login lockout, session
  resolve / absolute + idle expiry / logout invalidation, deterministic session-bound
  CSRF token, `set_role` (updates + invalidates sessions, rejects bad role/missing
  user), cookie helpers (HttpOnly+SameSite session cookie, JS-readable CSRF cookie,
  Secure when configured, clear-on-logout), schema idempotency, the full RBAC role
  matrix through the router (401 unauthenticated, public health, viewer/responder/admin
  gating), auth endpoints (login cookie shape, 401 wrong creds, **429 lockout**, me +
  logout), and the CSRF middleware (missing token → 403, foreign Origin → 403,
  valid same-origin → success).
- **test_hardening.py** (9) — S9 request-body cap (413 on oversized `Content-Length`,
  pass under cap), S10 SSE HEAD guard (no worker/body on HEAD, worker runs + connection
  closes on GET, `Connection: close` advertised), SEC2 generic error text (501 and bad
  JSON are non-revealing while genuine domain-validation messages survive), SEC3
  security headers on every response.
- **test_correctness.py** (11) — B1 (a `transition` to *resolved* retains + computes
  MTTR exactly like `resolve`, service + API), S1 non-null `root_cause` alias in the
  compare JSON, S2 remediation-steps type guard (reject a bare string, service + API),
  S3 duplicate `external_id` → 409 conflict + collision-resistant auto ids, S4
  not-found → 404 (vs genuine bad input still 400).

## CSRF self-heal regression (test_api_endpoints.py::TestCsrfSelfHeal)
The CSRF token is **server-derived** — `HMAC(server_secret, token_hash)` — not a pure
double-submit. If `MUNINN_SERVER_SECRET` is unset the process picks a random secret, so
after a restart a DB-backed session still authenticates but its stale `muninn_csrf`
cookie no longer matches and every mutation 403s. The regression proves the fix:
rotate the secret under a live session → a mutation 403s → `GET /api/auth/me` re-issues
a `muninn_csrf` cookie from the current secret (matching the body `csrf`) → the next
mutation echoing that token succeeds. `auth.js` runs `/api/auth/me` at boot, so the SPA
self-heals automatically.

## Key behaviors each stub must assert
- **retrieval**: cosine of identical text ≈ 1.0; unrelated ≈ low; fusion weights sum to
  1.0; a `queue` incident recalls its family's `memory` incident as top-1.
- **resolve → retain**: `memory.count()` increases by 1 after `IncidentService.resolve`;
  the retained document contains root cause + fix + signature; a subsequent recall of
  the same signature returns it.
- **agent robustness (FR-12)**: feeding a tool call with bad JSON args / unknown tool
  name / missing fields never raises — the agent returns a Brief (possibly degraded).
- **cold vs warm (FR-8)**: `warm` brief cites ≥1 past incident; `cold` cites none;
  both are produced without error even when the reasoner is the local fallback.
- **streaming (FR-13)**: the SSE endpoint emits ≥1 `data:` frame then a `done` event;
  the assembled text equals the non-streamed brief summary.
- **dataset**: every incident references a known service; `memory` rows carry a
  resolution; ≥1 `queue` incident shares a family with a `memory` row (true recall hit).

## Exit gate (see docs/PLAN.md milestone 10 / task #11)
**Met.** All tests are green (**119 OK, 0 skipped**); `compileall` is clean; every FR row
has an implemented, un-skipped test. The demo dry-run (sign up → seed → triage cold →
triage warm → resolve & remember → Insights) has been executed against the running
server and captured for the write-up.
