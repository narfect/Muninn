# How Muninn uses Hindsight

Hindsight (by Vectorize) is the agent-memory system at the core of Muninn. This document
is the required "how memory is used" deliverable: it maps Hindsight's concepts to
Muninn's incident workflow, shows the exact operations, and explains the offline fallback
that mirrors the same interface.

## Why memory is the product, not a feature

Muninn's thesis is that incident response should compound: each resolved outage should
make the next one faster. That requires durable, queryable, cross-incident memory with
good retrieval — exactly what Hindsight provides. Memory is on the critical path of every
triage, and it is visible in the UI (the recall pane, citations, the learning curve), so
the 25%-weighted "use of Hindsight" criterion is demonstrated, not asserted.

## Concepts → Muninn

| Hindsight concept | Muninn usage |
|---|---|
| **Memory bank** | one isolated bank, `muninn-incidents`, holds the org's incident corpus. |
| **retain** | on incident resolution, store an *experience* memory unit for the outcome. |
| **recall** | on a new alert, retrieve the most similar past incidents (TEMPR fusion). |
| **reflect** | synthesize patterns across recalled incidents into the triage brief. |
| **Fact types** | `experience` (resolved incidents/outcomes), `observation` (feedback, reinforcement), `world` (service topology / runbook facts, optional). |
| **Bank mission / disposition** | shapes reflect toward a calm senior-SRE voice (see `backend/llm/prompts.py`). |

## Bank configuration

Ensured once at startup, idempotently. Hindsight exposes a single **create-or-update
memory bank** call that creates the bank if it is absent, updates it in place if it
exists, and auto-fills any omitted field with a default:

```
PUT /v1/{namespace}/banks/{bank_id}
Authorization: Bearer <key>
{
  "reflect_mission": "You are the institutional memory for an on-call SRE team. Recall and
                      synthesize past incidents ...",
  "disposition_skepticism": 4,
  "disposition_literalism": 4,
  "disposition_empathy": 2
}
```

`bank_id` lives in the path; `namespace` defaults to `default`. The `reflect_mission`
string is sourced from configuration (`Settings.hindsight_bank_mission`,
env `HINDSIGHT_BANK_MISSION`) — not hard-coded in the client — and frames `reflect` as an
incident-memory analyst. The three `disposition_*` traits (1-5, default 3) are tuned for a
calm senior-SRE voice (`backend/memory/hindsight_store.py :: _DISPOSITION`): higher
skepticism and literalism (question assumptions, stay anchored to the evidence), lower
empathy (technical and composed). Earlier versions used a `POST /banks` + separate
`PUT .../profile` pair; that shape returned 405/422 and has been replaced by this one call.

**You do not need to pre-create a bank.** Hindsight auto-creates it with default settings
on the first *write* (retain / profile update / config change) — so if the startup PUT
does not confirm, the loop still works; only the custom disposition is skipped until a
subsequent successful update. (Reads of a bank that does not exist return 404, which is
why `HindsightStore` ensures the bank up front rather than on first recall.)

## 1. retain — the learning loop

When an incident is resolved (`backend/services/incidents.py :: resolve`), Muninn writes
a rich document so future recall is high-signal:

```
[INCIDENT INC-0042 · checkout-api · SEV1]
Symptom: 5xx spike after 14:02 deploy. Signature: http_5xx|deploy|conn_pool.
Root cause: release lowered the DB pool below peak; connections exhausted.
Fix: rolled back release 2024.10.3; raised pgbouncer pool to 200; added canary gate.
Runbook: RB-001. Resolver: payments-oncall. MTTR: 22m. Outcome: resolved, no recurrence.
```

```python
hs.retain(
    bank_id="muninn-incidents",
    content=document,                      # the text above
    # mem_type / metadata carried via the store wrapper:
    # type="experience", source="INC-0042",
    # metadata={"service": "checkout-api", "severity": "SEV1",
    #           "family": "A", "signature": "http_5xx|deploy|conn_pool", "mttr_minutes": 22}
)
```

REST: `POST /v1/{ns}/banks/{bank}/memories` with `{"items":[{"content": "...",
"document_id": "INC-0042"}]}` (supports `?async=true`). `document_id` is the **upsert
key** — retaining the same incident id replaces its prior memory instead of duplicating,
so re-seeding the bank is idempotent. The incident id is also embedded in the content
header so provenance survives even if optional metadata is not retained. Positive user
feedback (FR-9) retains a short `observation` that reinforces a correct diagnosis.

## 2. recall — grounding a new alert

On a new alert, Muninn builds a compact query from the incident and asks Hindsight for
the nearest prior incidents:

```python
query = incident.signature_text()
# e.g. "checkout-api: 5xx spike after deploy (http_5xx|deploy|conn_pool). ... tags: deploy, 5xx"
result = hs.recall(bank_id="muninn-incidents", query=query)   # top_k configurable
```

REST: `POST /v1/{ns}/banks/{bank}/memories/recall` with `{"query": "..."}`. Hindsight's
TEMPR retrieval fuses semantic + keyword (BM25) + graph + temporal signals and reranks,
returning `{"results": [{"text": ..., "id": ...}], ...}`. Muninn maps each hit to a
`Memory` (content from `text`, `source` = incident id from `document_id`/metadata) and
shows it in the recall pane; the agent cites these ids in the brief. This is what makes
the **Warm** brief specific and the **Cold** brief generic. (`top_k` is applied
client-side; the recall response does not document a per-item numeric score, so when none
is present the store ranks hits with a transparent descending value flagged
`score_source: rank_heuristic` in metadata — never a number dressed up as a backend
score.)

## 3. reflect — cross-incident patterns

For the synthesis line in the brief, Muninn asks Hindsight to reason over the bank:

```python
insight = hs.reflect(bank_id="muninn-incidents",
                     query="What recurring failure pattern does this alert match, and "
                           "what fix has worked before?")
```

REST: `POST /v1/{ns}/banks/{bank}/reflect` with `{"query": "..."}`. The synthesized answer
is returned in the top-level `text` field (supporting evidence under `based_on`). Reflect
is shaped by the bank mission/disposition toward actionable SRE guidance.

## The offline mirror (`LocalMemoryStore`)

The sandbox blocks external egress, and judges may run without keys, so Muninn ships a
faithful offline store implementing the **same `MemoryStore` interface**
(`retain/recall/reflect/count/health`). It persists memory units to a JSON file and
retrieves with a hybrid scorer (semantic hashed n-gram cosine + BM25 keyword + entity/tag
overlap + temporal decay, fused 0.45/0.30/0.15/0.10) — a small, honest local analogue of
TEMPR. Backend selection is automatic; the UI badge shows `hindsight` vs `local` so a
demo never misrepresents which memory served a result.

```
build_memory_store(settings) -> HindsightStore   # if base_url + api_key present
                             -> LocalMemoryStore   # otherwise (offline demo)
```

## Where this lives in the code

- `backend/memory/base.py` — the `MemoryStore` interface + `build_memory_store` factory.
- `backend/memory/hindsight_client.py` — the verified REST client (create_bank/retain/
  recall/reflect/health).
- `backend/memory/hindsight_store.py` — maps the interface onto the client.
- `backend/memory/local_store.py` + `retrieval.py` — the offline mirror + fusion scorers.
- `backend/services/incidents.py` — retain on resolve (learning loop).
- `backend/services/triage.py` — recall → agent → cited brief.
- `backend/services/metrics.py` — the learning curve, computed by replaying recall as the
  bank grows.

## Memory count (`count()`) — session-local caveat

`health.n_memories` and the metrics summary report `memory.count()`. The public Hindsight
API reference documents no per-bank memory-count/stats endpoint, so rather than fabricate
or guess a bank-wide total, `HindsightStore.count()` reports an **honest session-local
count**: the number of retains this process has performed. It resets to 0 on restart. The
learning-curve dashboard is unaffected — it is computed by replaying retrieval over the
DB-reconstructed incident corpus (`backend/services/metrics.py`), not from `count()`. A
demo that wants a stable total should keep the server process alive after seeding (or
re-seed), and the UI badge never presents the number as an authoritative server total.

## Verification status

The endpoint paths and request/response field names above were reconciled against the
official Hindsight API docs + OpenAPI (`hindsight.vectorize.io`) in October 2026 — notably:
bank setup is a single create-or-update `PUT /v1/{ns}/banks/{bank_id}` carrying
`reflect_mission` + `disposition_*`, recall is `POST .../memories/recall`, the reflect
answer lives in `text`, health is `GET /health`, and `document_id` drives upsert.

The live retain → recall → reflect loop was exercised end-to-end against the real
Hindsight service via `tests/test_hindsight_live_smoke.py` and **passed** (non-empty scored
recall hits + genuine reflect prose). That smoke test runs automatically whenever a key is
configured and the host is reachable, and skips cleanly otherwise — so it is green on an
operator machine with egress and skips in a sandbox without it (the Muninn build sandbox
blocks egress to the Hindsight host, so the live steps are verified on the operator's
machine, not from the sandbox). The offline suite (`tests/test_hindsight_store.py`) covers
the client/store behaviour — including the single-PUT create-or-update wire contract —
with these shapes and no network.

## Honesty note

All incident content is synthetic and labeled. Metrics (MTTR, learning curve) are
computed over that labeled demo dataset by replaying real recall calls — never
hand-waved. If Hindsight is unreachable, Muninn uses the local store and says so rather
than pretending a cloud call succeeded.
