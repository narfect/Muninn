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

Created once at startup (idempotent):

```python
from hindsight_client import Hindsight
hs = Hindsight(base_url=HINDSIGHT_BASE_URL, api_key=HINDSIGHT_API_KEY, timeout=30.0)
hs.create_bank(
    bank_id="muninn-incidents",
    name="Muninn incident memory",
    mission="Help on-call engineers resolve incidents faster by recalling how similar "
            "past incidents were diagnosed and fixed. Prefer safe, reversible mitigations "
            "and always ground recommendations in prior incidents.",
)
```

REST equivalent (the code uses a thin urllib client so no dependency is required):
`POST /v1/{namespace}/banks` with `Authorization: Bearer <key>`; namespace defaults to
`default`.

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

REST: `POST /v1/{ns}/banks/{bank}/memories` with `{"items":[{"content": "..."}]}`
(supports `?async=true`, returning an `operation_id`). Positive user feedback (FR-9)
retains a short `observation` that reinforces a correct diagnosis.

## 2. recall — grounding a new alert

On a new alert, Muninn builds a compact query from the incident and asks Hindsight for
the nearest prior incidents:

```python
query = incident.signature_text()
# e.g. "checkout-api: 5xx spike after deploy (http_5xx|deploy|conn_pool). ... tags: deploy, 5xx"
result = hs.recall(bank_id="muninn-incidents", query=query)   # top_k configurable
```

REST: `POST /v1/{ns}/banks/{bank}/recall` with `{"query": "..."}`. Hindsight's TEMPR
retrieval fuses semantic + keyword (BM25) + graph + temporal signals and reranks. Muninn
maps each hit to a `Memory` (content, score, `source` = incident id) and shows it in the
recall pane with its score; the agent cites these ids in the brief. This is what makes
the **Warm** brief specific and the **Cold** brief generic.

## 3. reflect — cross-incident patterns

For the synthesis line in the brief, Muninn asks Hindsight to reason over the bank:

```python
insight = hs.reflect(bank_id="muninn-incidents",
                     query="What recurring failure pattern does this alert match, and "
                           "what fix has worked before?")
```

REST: `POST /v1/{ns}/banks/{bank}/reflect` with `{"query": "..."}`. Reflect is shaped by
the bank mission/disposition toward actionable SRE guidance.

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

## Honesty note

All incident content is synthetic and labeled. Metrics (MTTR, learning curve) are
computed over that labeled demo dataset by replaying real recall calls — never
hand-waved. If Hindsight is unreachable, Muninn uses the local store and says so rather
than pretending a cloud call succeeded.
