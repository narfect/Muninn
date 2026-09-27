# CLAUDE.md — build guide for Muninn

This file orients a coding agent (Claude Code) working in this repo. Read it first, then
`docs/BUILD_SPEC.md` for the phased implementation plan.

## What Muninn is
An AIOps incident-response copilot with **institutional memory**. On a new alert it
recalls similar past incidents (root cause, fix, runbook, resolver, MTTR), an LLM agent
synthesizes a cited triage brief, and on resolution the outcome is retained so the
system improves. The demo centerpiece is a **memory ON/OFF (warm/cold) comparison** plus
**MTTR + learning-curve** dashboards. Built for the "Hack with Hyderabad 3.0" hackathon;
**Hindsight** (agent memory) is the mandatory, central technology.

## Golden rules (do not violate)
1. **Never fabricate** integrations, results, or evidence. If a backend isn't reachable,
   use the labeled offline fallback and say so in the UI/logs. Simulated data is always
   labeled "synthetic demo data".
2. **Offline-first**: the whole app must run and demo with **no API keys and no
   internet**, via `LocalMemoryStore` + `LocalReasoner`. Real Hindsight/Groq are
   first-class paths that activate when creds+network exist — same interfaces.
3. **Standard library only** for the backend (`http.server`, `sqlite3`, `urllib`,
   `json`, `unittest`) plus preinstalled `numpy`/`requests` if genuinely helpful. No pip
   installs (registry is blocked). Frontend is vanilla JS/CSS — no npm, no CDN.
4. **Keep the test suite green.** `python -m unittest discover -s tests` must pass; turn
   the `@unittest.skip` stubs into real tests as you implement each feature (don't delete
   them).
5. **Verify before claiming done.** Run compileall + tests + a manual demo dry-run.
6. Don't expose secrets; `.env` is git-ignored; only `.env.example` is committed.

## Layout
```
backend/
  config.py        # CONCRETE: env-driven Settings, backend resolution
  models.py        # CONCRETE: dataclasses (Incident, Memory, RecallResult, Brief, ...)
  db.py            # CONCRETE: sqlite schema + Repository
  router.py        # CONCRETE: Request/Response, path routing, 404/501/500 handling
  server.py        # CONCRETE: wiring + threaded HTTP server + static serving
  api/routes.py    # STUBS: endpoint handlers (contract in docstring) + concrete route table
  memory/          # STUBS: hindsight_client, hindsight_store, local_store, retrieval (+ base CONCRETE)
  llm/             # STUBS: client (Groq+Local), tools, agent (+ prompts near-complete)
  services/        # STUBS: incidents (learning loop), triage (orchestration), metrics
static/            # CONCRETE contract: index.html shell, styles.css tokens, app.js api client + stubs
data/seed_sample.json  # CONCRETE: labeled synthetic sample (expand to ~60 per docs/DATASET.md)
tests/             # runnable stdlib suite; concrete layers green, rest skipped-with-spec
docs/              # SRS, ARCHITECTURE, PLAN, MVP, DATASET, UI_SPEC, TEST_PLAN, BUILD_SPEC, HINDSIGHT
```
Every stub method carries an `IMPLEMENT (Claude Code):` spec in its docstring. The
concrete files define the contracts you build against — don't change their signatures
without reason.

## Commands
```
python -m backend.server                       # run (serves API + UI on 127.0.0.1:8000)
python -m unittest discover -s tests -v         # tests
python -m compileall backend tests              # import/syntax gate
```
Config via env or `.env` (see `.env.example`): `HINDSIGHT_BASE_URL/API_KEY`,
`GROQ_API_KEY`, `MUNINN_MEMORY_BACKEND=auto|hindsight|local`, `MUNINN_LLM_BACKEND`.

## Hindsight usage (the 25% criterion — make it visible)
- `retain` resolved incidents as `experience` memories (rich doc: symptom, signature,
  root cause, fix, runbook, resolver, MTTR; `source=external_id`).
- `recall` on a new alert's `signature_text()` → show scored, cited matches in the UI.
- `reflect` for cross-incident pattern synthesis in the brief.
See `docs/HINDSIGHT.md` and `docs/ARCHITECTURE.md` for the concept→code mapping, and the
verified REST/SDK details in the memory client docstrings.

## Build order
Follow `docs/BUILD_SPEC.md` phases (memory → reasoner/agent → services → API → UI →
metrics → polish). Each phase lists its acceptance test.
