# Claude Code — Handoff Prompts (ready to paste)

How to use: open this repo in Claude Code and paste the **Master prompt** first. Then, if
you prefer tighter control, paste the **phase prompts** one at a time. Everything Claude
Code needs is already in the repo (`CLAUDE.md`, `docs/BUILD_SPEC.md`, stub docstrings,
green test scaffold). You (neha) don't need to write any code.

Optional before you start: copy `.env.example` to `.env` and add `GROQ_API_KEY` and/or
`HINDSIGHT_BASE_URL`+`HINDSIGHT_API_KEY` to light up the real backends. It also runs
fully offline with no keys.

---

## Master prompt (paste this first)

```
You are implementing "Muninn", an AIOps incident-response copilot with institutional
memory, for a hackathon where Hindsight (agent memory) is the mandatory, central tech.

The repo is a planned, import-safe scaffold. Read CLAUDE.md and docs/BUILD_SPEC.md, then
implement the feature stubs phase by phase exactly as BUILD_SPEC describes. Every stub
carries an "IMPLEMENT (Claude Code):" spec in its docstring — follow it.

Hard rules:
- Offline-first: the app must run and demo with NO API keys and NO internet, via
  LocalMemoryStore + LocalReasoner. Real Hindsight/Groq are first-class paths that turn
  on when creds+network exist, using the same interfaces. Never fabricate results; if a
  backend is unreachable, fall back and say so. Keep synthetic data labeled.
- Standard library only for the backend; vanilla JS/CSS for the frontend; no pip/npm
  installs (registries are blocked).
- Keep the test suite green. Turn the @unittest.skip stubs in tests/ into real passing
  tests as you implement each feature — don't delete or weaken them.
- After each phase: run `python -m compileall backend tests` and
  `python -m unittest discover -s tests -v`, then do a quick manual check.

Work through BUILD_SPEC phases 1→7. Start with Phase 1 (memory layer). Show me the diff
and the test output after each phase before moving on.
```

---

## Phase prompts (optional, one at a time)

### Phase 1 — memory
```
Implement Phase 1 (memory layer) from docs/BUILD_SPEC.md: backend/memory/retrieval.py,
local_store.py, hindsight_client.py, hindsight_store.py, per their docstrings. Then
un-skip and pass tests/test_retrieval.py and tests/test_memory_local.py. Keep everything
runnable with MUNINN_MEMORY_BACKEND=local. Show test output.
```

### Phase 2 — reasoner + agent
```
Implement Phase 2 (backend/llm/client.py, tools.py, agent.py). GroqClient must tolerate
function-calling errors and never raise; LocalReasoner must produce a usable brief
offline; the agent loop is capped and degrades gracefully. Un-skip and pass
tests/test_agent.py, including malformed-tool-call robustness (FR-12).
```

### Phase 3 — services
```
Implement Phase 3 (backend/services/incidents.py, triage.py, metrics.py): lifecycle,
resolve→RETAIN learning loop (use the retention template in docs/DATASET.md), triage
orchestration with provenance, and MTTR + learning-curve metrics. Add the seed loader for
data/seed_sample.json (compute timestamps from created_days_ago). Un-skip and pass
tests/test_services.py; confirm memory.count() increases by 1 on resolve.
```

### Phase 4 — API
```
Implement Phase 4: every handler in backend/api/routes.py per its docstring contract,
including demo seed/reset and the SSE streaming triage endpoint. Validate inputs and
return correct status codes. Un-skip and pass tests/test_api_endpoints.py. Verify
`POST /api/demo/seed` then `GET /api/incidents` is non-empty and `/api/compare` shows
warm-with-citations vs cold-without.
```

### Phase 5 — frontend
```
Implement Phase 5: the render functions in static/app.js against docs/UI_SPEC.md and the
API — queue, active incident + Cold/Warm hero toggle, triage brief with citation chips
linked to the recall pane, the recall pane, the cold-vs-warm compare, resolve&remember,
and Insights. Use the existing design tokens in styles.css; add only component styles.
Then walk the MVP demo path (docs/MVP.md steps 1–6) in a browser and confirm it works.
```

### Phase 6 — viz + polish, Phase 7 — verify
```
Implement Phase 6 (canvas/SVG MTTR + learning-curve charts, reflect panel, a11y polish)
and Phase 7 (full verification): compileall clean, unittest green with no skips left
among the MVP "Must" features, and a scripted dry-run seed→cold→warm→resolve→curve.
Capture the output for the demo video and update docs/DEMO_SCRIPT.md.
```

---

## Definition of done to confirm back to me
- Runs offline (no keys) and the demo path works; real backends work when keys are set.
- `python -m unittest discover -s tests` green, no skips among MVP Must features.
- No fabricated results anywhere; synthetic data labeled.
- docs/HINDSIGHT.md matches the implemented memory usage.
