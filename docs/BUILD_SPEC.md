# Muninn — BUILD_SPEC (phased implementation plan for Claude Code)

This is the execution plan. The repo is a **planned, import-safe scaffold**: contracts
(config, models, db, router, server, static tokens, dataset) are concrete; feature
bodies are `IMPLEMENT (Claude Code):` stubs. Your job is to fill the stubs, phase by
phase, keeping the suite green and the app runnable at every step.

Read `CLAUDE.md` (golden rules) and `docs/ARCHITECTURE.md` first. Requirement IDs (FR-n)
refer to `docs/SRS.md`. UI details are in `docs/UI_SPEC.md`; data in `docs/DATASET.md`.

## Working method
- Implement one phase at a time. After each, run `python -m compileall backend tests`
  and `python -m unittest discover -s tests -v`, and convert that phase's skipped tests
  into real, passing tests (keep the test names/intent).
- Never break a green test to make progress. Never fabricate a passing result.
- Keep both backends working: everything must run with `MEMORY_BACKEND=local` and
  `LLM_BACKEND=local` (no network). Test against those; gate real-backend calls on creds.
- Commit per phase with a clear message.

## Non-negotiables (recap)
Offline-first · stdlib-only backend · vanilla JS frontend · no fabricated results ·
label synthetic data · FR-12 tolerate malformed tool calls · honest backend badges.

---

## Phase 0 — sanity (30 min)
- `python -m backend.server` starts; `/` serves the shell; `/api/health` returns 501.
- `python -m unittest discover -s tests` → green with skips.
- Read every stub docstring in `backend/memory`, `backend/llm`, `backend/services`,
  `backend/api`. They are the spec.

## Phase 1 — Memory layer (the heart; FR-2/3/5)
Files: `backend/memory/retrieval.py`, `local_store.py`, `hindsight_client.py`,
`hindsight_store.py`.
Implement:
- `retrieval.py`: the four scorers (semantic hashed n-gram cosine, BM25 keyword,
  temporal decay, entity/tag overlap) + weighted fusion (0.45/0.30/0.15/0.10) returning
  a fused score in [0,1] and a per-strategy breakdown for explainability.
- `local_store.py`: `LocalMemoryStore` — JSON-file persistence, `retain/recall/reflect/
  count/health`, using the retriever. `reflect` = a deterministic summary over top recall
  (offline). This is the demo default.
- `hindsight_client.py`: faithful REST client (urllib) — create_bank/retain/recall/
  reflect/health per the verified endpoints in the docstring; defensive parsing.
- `hindsight_store.py`: `HindsightStore` mapping the `MemoryStore` interface onto the
  client; `recall` maps API hits → `Memory` objects with score+provenance.
Acceptance: un-skip and pass `tests/test_retrieval.py` and `tests/test_memory_local.py`;
`build_memory_store(settings)` returns the right backend for each config.

## Phase 2 — Reasoner + agent (FR-11/12)
Files: `backend/llm/client.py`, `tools.py`, `agent.py` (prompts.py is ready).
Implement:
- `GroqClient.chat`: OpenAI-compatible call via requests; tolerate function-calling
  errors; never raise (return `{"content":..., "tool_calls":[...]}` or an error content).
- `LocalReasoner.chat`: deterministic offline reasoner that, given recalled memories,
  emits a sensible brief + can "call" the recall tool — enough to demo without Groq.
- `tools.py`: `TOOL_SPECS` for recall_memory/reflect_memory/get_runbook/get_service and
  `dispatch(...)` that returns `{"error":...}` (never raises) on unknown tool/bad args.
- `agent.py`: the tool-calling loop (cap ~4 iters) → parse final into `Brief`; on parse
  failure, degrade to a Brief built from recalled memories. Memory-OFF path skips recall.
Acceptance: un-skip and pass `tests/test_agent.py` (incl. malformed-tool-call robustness).

## Phase 3 — Services: lifecycle, learning loop, metrics (FR-1/4/6/7/9/14..16)
Files: `backend/services/incidents.py`, `triage.py`, `metrics.py`.
Implement:
- `incidents.py`: `create_incident` (validate + normalize + compute `error_signature` +
  timeline "opened"); `transition` (enforce open→triaging→mitigated→resolved); `resolve`
  (set resolved_at + mttr_minutes, then **RETAIN** a rich experience doc — the learning
  loop, see the retention template in `docs/DATASET.md`); `record_feedback` (store +
  reinforce on positive). Also a `seed(data)` path that loads `data/seed_sample.json`
  (compute timestamps from `created_days_ago`, retain `memory`-role incidents).
- `triage.py`: `triage(incident, use_memory, stream)` = recall → agent → Brief (+ attach
  provenance by matching recalled memories back to incident ids); `compare` = cold+warm.
- `metrics.py`: `mttr`, `learning_curve` (replay resolved incidents chronologically,
  recall against only-earlier memories, record top-1 score + hit — a real curve), and
  `summary`. Metrics computed over the labeled demo dataset only.
Acceptance: un-skip and pass `tests/test_services.py` (lifecycle, resolve→retain,
metrics). `memory.count()` rises by 1 on resolve.

## Phase 4 — API endpoints + seed + streaming (FR-8/13; all routes)
File: `backend/api/routes.py` (route table is already concrete).
Implement every handler per the endpoint contract in the module docstring: health,
services, runbooks, incident CRUD/transition/resolve/feedback, triage (+ SSE stream via
`Response.sse`), compare, memory recall/reflect, metrics, demo seed/reset. Validate
inputs; return correct status codes; label demo data.
Acceptance: un-skip and pass `tests/test_api_endpoints.py`; `POST /api/demo/seed` then
`GET /api/incidents` is non-empty; `POST /api/compare` warm cites past incidents, cold
doesn't; SSE emits ≥1 frame then a `done` event.

## Phase 5 — Frontend renderers (FR-17/18; UX 15%)
File: `static/app.js` (api client, state, router, boot are concrete). Implement the
render stubs against `docs/UI_SPEC.md` and the endpoints above: `renderQueue`,
`renderActive` (+ Cold/Warm hero toggle + Run triage), `runTriage` (non-stream first,
then wire `api.stream`), `renderBrief` (citations as `.cite` chips linked to recall
rows), `renderRecall`, `renderCompare` (the side-by-side money shot), `resolveAndRemember`,
`renderInsights`. Keep the design tokens in `styles.css`; add only component styles.
Acceptance: manual demo path (MVP steps 1–6) works in a browser against the local
backend; keyboard nav + reduced-motion respected; empty state shows the seed invitation.

## Phase 6 — Metrics visualization + polish
Draw MTTR + learning-curve with `<canvas>` or inline SVG (no chart lib — none installable).
Add the reflect panel and timeline if time allows (SHOULD in `docs/MVP.md`). Tighten
a11y (contrast, focus, aria-live on the streaming brief). Verify honest backend badges.

## Phase 7 — Verify + demo dry-run (task #11 gate)
- `python -m compileall backend tests` clean; `python -m unittest discover -s tests` green
  with **no skips left among MVP "Must" features**.
- Scripted dry-run: seed → triage cold → triage warm → resolve → learning-curve; capture
  output for the demo video and README screenshots.
- Fill `docs/DEMO_SCRIPT.md` timings; confirm `docs/HINDSIGHT.md` matches the code.

---

## Definition of done (whole build)
MVP demo path works fully offline; real Hindsight + Groq paths work when creds+network
are present; suite green; docs match the code; no fabricated results anywhere; synthetic
data clearly labeled. Then record the video and write the article/social post.

## File → phase quick index
| phase | files | acceptance test |
|---|---|---|
| 1 | memory/{retrieval,local_store,hindsight_client,hindsight_store}.py | test_retrieval, test_memory_local |
| 2 | llm/{client,tools,agent}.py | test_agent |
| 3 | services/{incidents,triage,metrics}.py | test_services |
| 4 | api/routes.py | test_api_endpoints |
| 5 | static/app.js (+ styles.css components) | manual MVP path |
| 6 | static/ charts, a11y | manual + a11y check |
| 7 | — | full suite + dry-run |
