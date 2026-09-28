# Implementation Plan — Muninn

## 1. Milestones (build order)

1. **Foundations** — config, db schema, dataclasses, memory `base` interface.
2. **Memory layer** — `HindsightClient` (REST), `HindsightStore`, `LocalMemoryStore`
   + `retrieval` utilities. Unit tests for retrieval + local store.
3. **LLM layer** — `Reasoner` interface, `GroqClient`, `LocalReasoner`, tool schema,
   `TriageAgent` loop with function-calling error handling. Tests for the agent loop.
4. **Services** — incident lifecycle, triage orchestration (recall+reflect+agent),
   metrics (MTTR, learning curve, coverage).
5. **API + server** — routes, JSON/SSE, static serving, health (reports backends).
6. **Dataset** — deterministic generator + seeder (services, runbooks, ~60 incidents,
   demo scenarios).
7. **Frontend** — operator console SPA (feed, war room, memory panel, dashboards,
   demo controls, backend badges).
8. **Verify** — run server, seed, exercise all endpoints, run unittest suite, fix.
9. **Docs & content** — README, HINDSIGHT.md, DEMO_SCRIPT.md, article, social post,
   video script, pitch deck, screenshots.
10. **Quality gate** — judge-POV review against criteria; final report.

## 2. Traceability (requirement → module → test)

| FR | Module(s) | Test |
|----|-----------|------|
| FR-1..4 lifecycle | `services/incidents.py`, `db.py`, `api/routes.py` | `test_api.py`, `test_incidents.py` |
| FR-5 recall | `memory/*`, `services/triage.py` | `test_memory_local.py`, `test_retrieval.py` |
| FR-6 retain | `memory/*`, `services/incidents.py` | `test_memory_local.py` |
| FR-7 reflect | `memory/*`, `llm/agent.py` | `test_agent.py` |
| FR-8 toggle | `services/triage.py`, `api/routes.py` | `test_api.py` |
| FR-9 feedback | `services/triage.py`, `memory/*` | `test_memory_local.py` |
| FR-11..13 agent | `llm/agent.py`, `llm/tools.py` | `test_agent.py` |
| FR-12 fn-call errors | `llm/agent.py` | `test_agent.py::test_bad_tool_calls` |
| FR-14..16 analytics | `services/metrics.py` | `test_metrics.py` |
| FR-17..18 UI | `static/*`, `api/routes.py` | manual + `test_api.py` (serves SPA) |

## 3. Mapping to judging criteria

- **Innovation (30%)** — institutional memory for on-call; a measurable learning loop
  and MTTR impact rather than a chatbot. Fresh framing: "capture the tribal knowledge
  that walks out the door when your senior SRE quits."
- **Use of Hindsight memory (25%)** — all three ops used meaningfully; before/after
  toggle and learning-curve chart make memory *visible*; typed memories; feedback loop.
- **Technical implementation (20%)** — layered, typed, tested; robust error handling
  incl. LLM function-calling errors; two interchangeable backends; SSE streaming.
- **User experience (15%)** — story-driven console; 60-second demo; provenance and
  backend transparency; polished, distinctive dark "mission control" design.
- **Real-world impact (10%)** — MTTR reduction, onboarding, hero-dependency reduction;
  clear adoption path alongside PagerDuty/Datadog.

## 4. Risks & mitigations
- *External services unavailable* → interchangeable local backends; UI shows mode.
- *LLM returns malformed tool calls* (guide warns) → defensive parsing + retry + safe
  fallback path; covered by a dedicated test.
- *Demo flakiness* → scripted scenarios + deterministic dataset + offline local mode.
- *Scope creep* → one workflow (first-responder triage) done well; everything serves
  the before/after memory story.
