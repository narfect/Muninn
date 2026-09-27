# Muninn — MVP Definition

The minimum version that tells the whole story to a judge in a 3-minute demo, mapped to
the judging criteria (Innovation 30 · Use of Hindsight 25 · Technical 20 · UX 15 ·
Impact 10). Anything not needed for that story is out of MVP scope.

## The one sentence
On a new production alert, Muninn recalls what the org learned from past incidents and
drafts a cited triage brief — then remembers the outcome so it gets faster over time.

## The demo happy path (this must work end-to-end)
1. **Seed** the labeled demo dataset → the memory bank fills with ~45 resolved incidents.
2. Open a **new SEV1** alert from the queue (e.g. INC-0058, checkout 5xx after deploy).
3. Toggle **Cold** → triage brief with no memory: generic, low confidence, no citations.
4. Toggle **Warm** → Muninn recalls INC-0007/0021/0039 (same family), and the brief now
   names the likely root cause (DB pool regression), the exact fix, runbook RB-001, and
   **cites the past incidents**. This side-by-side is the money shot.
5. **Resolve & remember** the incident → a new memory is written; the counter ticks up.
6. **Insights**: MTTR trends down and the **learning curve** rises as the bank grows.

If steps 1–6 run on a laptop with no API keys and no internet, the MVP is met.

## Scope

### Must (MVP — required for the demo and the criteria)
- Hindsight-shaped memory layer with **retain / recall / reflect**, real Hindsight client
  when creds exist and a faithful **offline LocalMemoryStore** otherwise (same interface).
- Recall with explainable **fusion scoring** (semantic + keyword + entity + temporal).
- Agent triage producing a **Brief** (root cause, steps, runbook, citations, confidence)
  via Groq when available, **LocalReasoner** fallback otherwise. FR-12 robustness.
- **Memory ON/OFF compare** (cold vs warm) — the innovation centerpiece.
- Incident lifecycle + **resolve → retain** learning loop (+ feedback).
- **MTTR + learning-curve** metrics over the labeled demo dataset.
- Three-pane console UI + Insights, dependency-free, served by the backend.
- Green stdlib test suite; honest backend badges; clear "synthetic demo data" labeling.

### Should (strong bonus, build if time remains)
- SSE token **streaming** of the brief (FR-13).
- Reflect panel ("ask memory for patterns").
- Per-incident timeline view.

### Could (nice-to-have, explicitly deferred)
- Auth/multi-user; real alert-source webhooks (PagerDuty/Prometheus); export to PDF;
  multiple memory banks per team; vector-DB swap. All out of MVP.

### Won't (this hackathon)
- Real production data or live integrations; anything that could misrepresent simulated
  results as real telemetry.

## Definition of done (MVP)
Demo path 1–6 works offline; `python -m unittest discover -s tests` is green with no
skipped test among the MVP "Must" features; `docs/HINDSIGHT.md` explains memory usage;
README quick-start brings it up in one command; the before/after and learning-curve are
visible on screen. Ship state tracked in docs/PLAN.md milestones.
