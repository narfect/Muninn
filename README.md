<h1 align="center">🐦‍⬛ Muninn</h1>
<p align="center"><em>The incident-response copilot that remembers.</em></p>
<p align="center">
  Recall what your org already learned from past outages — root cause, the exact fix,
  the runbook, the resolver, the MTTR — the moment a new alert fires.
</p>

---

> **Status:** feature build complete. Accounts + role-based access, the Hindsight
> learning loop (retain/recall/reflect), the cold⇄warm triage comparison, streaming
> briefs, and the MTTR + learning-curve dashboards are all implemented and verified
> (server boots, full test suite green). Runs fully offline; all incident data is
> **synthetic and labeled**.

## Why Muninn

On-call engineering has a memory problem. The same incident recurs months apart and the
next responder starts from zero — even though someone already diagnosed and fixed it. The
knowledge is scattered across chat threads, postmortems, and people who since changed
teams. Mean-time-to-resolve pays the price.

Muninn treats **institutional memory as a first-class system**. Every resolved incident
is *retained*; every new alert *recalls* the most similar past incidents and an LLM agent
drafts a **cited triage brief**. The longer it runs, the better it gets. In Norse myth,
Odin's raven **Muninn** ("memory") flies the world and returns with what it has seen — the
name is the product thesis.

Memory is powered by **[Hindsight](https://hindsight.vectorize.io)**, the mandatory
agent-memory technology for this hackathon, used for its three core operations —
**retain / recall / reflect** — as the heart of the system, not a bolt-on.

## The one demo that tells the story

A single toggle — **Cold ⇄ Warm** — triages the same live alert twice:

| | **Cold** (memory off) | **Warm** (memory on) |
|---|---|---|
| Root cause | generic guess, low confidence | names the DB-pool regression from prior incidents |
| Fix | "investigate…" | the exact rollback + `pgbouncer` bump that worked before |
| Citations | none | `INC-0007`, `INC-0021`, `INC-0039` (same family) |
| Runbook | — | `RB-001` |

Then **Resolve & remember** writes a new memory, the counter ticks up, and the
**Insights** view shows MTTR trending down and a **learning curve** rising as the bank
grows. (All over a clearly-labeled synthetic dataset.)

## Quick start

Muninn runs **fully offline** — no API keys, no internet, no install. All you need is
**Python 3.10+**.

```bash
git clone https://github.com/narfect/Muninn muninn && cd muninn
python3 -m backend.server
```

Then open **http://127.0.0.1:8000**. (Prefer `make`? `make run` does the same thing, and
`make help` lists every target.)

The app boots in **open demo mode** — no login needed. The queue is already seeded with
labeled synthetic incidents, and a status-bar switcher lets you act as **Viewer / Responder
/ Admin** (each mints a real session for that role). Pick a SEV1 alert and flip
**Cold ⇄ Warm**, or hit **Compare cold vs warm** — that's the whole thesis in one click.

Roles gate what the UI offers and what the server allows: **viewer** reads incidents,
recalled memory, and can run triage/compare; **responder** can also create and resolve
incidents; **admin** can seed/reset data and manage user roles. The client only hides
controls it can't use — the server still enforces every check.

Demo mode is **local-only and self-disabling**: set `MUNINN_SERVER_SECRET` (the production
signal) and it switches off automatically — the sign-in gate returns and **the first
account created becomes the admin**. The full running guide, per-role capability table, and
a judge walkthrough are in [`docs/USING_MUNINN.md`](docs/USING_MUNINN.md).

### Stop & restart

The server runs in the foreground, so **Ctrl-C** stops it. To run it in the background
(handy for a demo so you keep the terminal), redirect its log and background it:

```bash
PYTHONPATH="$PWD" python3 -m backend.server > run.log 2>&1 &
```

Stop a backgrounded server with:

```bash
pkill -f backend.server
```

Restarting is safe even with the browser tab still open — the SPA automatically
re-establishes its session, so you never see a stale-token error after a restart.

### Run the tests

```bash
python3 -m unittest discover -s tests -q
```

On **macOS**, raise the open-file limit first (the full serial run opens many SQLite/WAL
file descriptors and the default limit of 256 is too low):

```bash
ulimit -n 8192 && python3 -m unittest discover -s tests -q
```

`make test` runs the suite and `make compile` is the import/syntax gate.

### Turn on the real backends (optional)

```bash
cp .env.example .env
```

Then edit `.env` and set:

- `GROQ_API_KEY` — real LLM reasoning via Groq (OpenAI-compatible).
- `HINDSIGHT_BASE_URL` + `HINDSIGHT_API_KEY` — the real Hindsight memory service.

Backend selection is automatic (`auto`): real service when credentials are present,
offline fallback otherwise. The UI badges always show which path is live (`hindsight`/
`local`, `groq`/`local`) so a demo is never misleading.

**Pacing a live demo:** Groq's free tier allows about 8000 tokens per minute. Rapid
back-to-back triages can briefly hit that ceiling — the client retries and honors the
rate-limit backoff, and any single call that can't reach a live backend degrades to the
labeled offline fallback rather than failing. If you're sweeping many incidents, give it a
few seconds between runs for the snappiest, always-live experience.

For any non-local deployment, also set **`MUNINN_SERVER_SECRET`** to a strong random value
(it keys the per-session CSRF tokens) and **`MUNINN_COOKIE_SECURE=true`** when serving over
HTTPS. Session lifetimes and login-lockout thresholds are configurable too — see the
commented **Auth & sessions** block in [`.env.example`](.env.example).

## How Hindsight is used (the memory core)

| Hindsight op | In Muninn |
|---|---|
| **retain** | On resolve, store a rich *experience* memory: symptom, error signature, root cause, exact fix, runbook, resolver, MTTR (`source = incident id`). |
| **recall** | On a new alert, query with the incident's signature; get scored, cited past incidents via TEMPR-style fusion (semantic + keyword + entity + temporal). |
| **reflect** | Synthesize cross-incident patterns ("we've seen this family 4× since June") into the brief. |

Memory banks isolate the incident corpus (`muninn-incidents`). The offline
`LocalMemoryStore` mirrors the same interface with a hybrid retriever so the concept is
demonstrable without the network. Full detail: [`docs/HINDSIGHT.md`](docs/HINDSIGHT.md).

## Architecture (at a glance)

```
Browser SPA (vanilla JS)  ──HTTP/JSON + SSE──▶  Python stdlib server (http.server)
                                                   │
                    ┌──────────────────────────────┼───────────────────────────┐
                    ▼                               ▼                           ▼
              Services layer                  Memory layer                 Reasoner
     (incidents · triage · metrics)   MemoryStore: Hindsight | Local   Groq | Local
                    │                               │
                    ▼                               ▼
             SQLite (system of record)     retain / recall / reflect
```

Two swappable seams — `MemoryStore` and `Reasoner` — mean the same code path runs against
real cloud services or fully offline. SQLite is the system of record; Hindsight is the
semantic memory. Details and diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Tech stack & why

- **Backend:** Python 3.10 standard library only (`http.server`, `sqlite3`, `urllib`,
  `json`). Zero-install is a feature: judges run it in one command, anywhere.
- **Frontend:** dependency-free vanilla JS/CSS SPA, served by the backend. No build step.
- **Memory:** Hindsight (cloud/OSS) with a faithful offline fallback.
- **LLM:** Groq (OpenAI-compatible) with an offline deterministic fallback.
- **Tests:** stdlib `unittest`.

## Project structure

```
backend/    config·models·db·router·server (concrete) + memory·llm·services·api
static/     index.html · styles.css (design tokens) · app.js · auth.js (SPA + api client)
data/       seed_sample.json (labeled synthetic dataset)
tests/      stdlib unittest suite (memory · retrieval · agent · services · api ·
            auth · hardening · correctness)
docs/       SRS · ARCHITECTURE · PLAN · MVP · HINDSIGHT · TEST_PLAN · USING_MUNINN
```

## Testing & quality

```bash
make compile
make test
```

`make compile` is the import/syntax gate (compileall); `make test` runs the full unittest
suite.

Every layer ships with green tests — memory (local + Hindsight parity), retrieval, the
agent (including malformed-tool-call handling), services, the API endpoints, auth/RBAC,
transport hardening, and correctness regressions (see
[`docs/TEST_PLAN.md`](docs/TEST_PLAN.md)). Principles: offline-deterministic, no network
in unit tests, no fabricated passes.

## Judging-criteria fit

- **Innovation (30%)** — memory-as-a-system; the cold/warm before-after; a visible
  learning curve.
- **Use of Hindsight (25%)** — retain/recall/reflect are the core loop, surfaced in the UI.
- **Technical (20%)** — clean layered architecture, swappable backends, robust agent
  (tolerates malformed tool calls), tested.
- **UX (15%)** — a calm, purpose-built ops console; the whole thesis is one toggle.
- **Real-world impact (10%)** — MTTR reduction is the daily pain of every on-call team.

## Project status

The feature build is **complete and verified**. On top of the concrete contract layers
(config, models, DB, router, server, design tokens, dataset) the full application is
implemented:

- **Institutional memory** — retain / recall / reflect through a `MemoryStore` seam, with a
  real Hindsight client and a faithful offline `LocalMemoryStore` behind the same interface.
- **Agentic triage** — a tool-using reasoner (Groq or offline `LocalReasoner`) that emits a
  cited brief, streamed token-by-token over SSE, and tolerates malformed tool calls.
- **The learning loop** — resolving an incident retains a new memory and the counter ticks up.
- **Accounts + RBAC** — email/password auth, HttpOnly session cookies, double-submit CSRF,
  failed-login lockout, and a viewer/responder/admin role hierarchy enforced server-side
  (first account created becomes admin).
- **Dashboards** — MTTR by service and a learning curve, with accessible data-table fallbacks.

Everything runs under the original constraints: Python standard library only, dependency-free
vanilla-JS frontend, offline-first, no fabricated data (backends degrade to labeled offline
fallbacks), and a green `unittest` suite. All incident data is **synthetic** and labeled as
such. 

