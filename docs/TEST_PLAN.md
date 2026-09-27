# Muninn — Test Plan

Status: **plan + runnable scaffold**. The scaffold in `tests/` runs today with
`python -m unittest discover -s tests -v`. Tests for the concrete contract layers
(models, config, router, dataset) are **real and green now**; tests for behavior that
Claude Code implements are present as `@unittest.skip("IMPLEMENT ...")` stubs carrying
the exact assertions to write. Removing each skip is a checklist item as features land.

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
| FR / area | test file | status |
|---|---|---|
| Domain models, signature_text, as_dict | test_models.py | green |
| Config backend resolution (auto/forced) | test_config.py | green |
| Router: path params, 404, 501, JSON errors | test_router.py | green |
| Dataset integrity (schema, recurrence) | test_dataset.py | green |
| FR-2/3 recall + fusion scoring | test_retrieval.py | skip → implement |
| FR-5 retain / round-trip (LocalMemoryStore) | test_memory_local.py | skip → implement |
| FR-1/6/7 incident lifecycle + transitions | test_services.py | skip → implement |
| FR-4 resolve → RETAIN to memory (learning) | test_services.py | skip → implement |
| FR-14..16 MTTR + learning-curve + coverage | test_services.py | skip → implement |
| FR-11 agent tool-loop → Brief | test_agent.py | skip → implement |
| FR-12 malformed tool-call robustness | test_agent.py | skip → implement |
| FR-8 memory ON/OFF compare (cold vs warm) | test_api_endpoints.py | skip → implement |
| FR-9 feedback reinforces memory | test_api_endpoints.py | skip → implement |
| FR-13 SSE streaming triage | test_api_endpoints.py | skip → implement |
| API endpoints happy-path + validation | test_api_endpoints.py | skip → implement |

## Key behaviors each stub must assert (for Claude Code)
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
All non-skipped tests green; `compileall` clean; each FR row has an implemented,
un-skipped test before the feature is called "done". A demo dry-run (seed → triage cold
→ triage warm → resolve → learning-curve) is executed and captured for the video.
