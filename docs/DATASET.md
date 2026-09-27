# Muninn — Synthetic Dataset Spec ("Northwind")

Status: **spec + committed sample**. `data/seed_sample.json` is a valid, runnable slice
(all services + runbooks + a representative set of incidents with real recurrence).
Claude Code expands it to the full volume below, or writes a generator that emits the
same JSON shape. **All data is synthetic and must be labeled as such in the UI and API**
(NFR transparency): the `meta.note` field carries the disclaimer; never present it as
real telemetry.

## Why this dataset exists
Muninn's whole thesis is that recall of *past* incidents improves triage of *new* ones.
That only demonstrates if the data contains genuine **recurring incident families** —
outages that rediscover the same root cause months apart. The dataset is engineered so
that (a) new alerts have true nearest-neighbors to recall, and (b) recurring families
resolve progressively faster once the memory/runbook exists — which is what the
learning-curve and MTTR-improvement metrics measure. The trend is baked into the
synthetic `mttr_minutes` honestly and labeled as a demo dataset.

## The platform
A fictional e-commerce platform, **Northwind**. 8 services across 3 tiers, sharing
infrastructure (a `postgres-primary`, a `redis-cache`, `kafka`) so that failures
plausibly correlate across services.

### Services (8) — fields match `models.Service`
| name | tier | dependencies | oncall |
|---|---|---|---|
| checkout-api | 1 | payments-svc, cart-svc, identity-svc | payments-oncall |
| payments-svc | 1 | postgres-primary, stripe-gateway | payments-oncall |
| identity-svc | 1 | postgres-primary, redis-cache | platform-oncall |
| cart-svc | 2 | redis-cache, catalog-svc | commerce-oncall |
| catalog-svc | 2 | postgres-primary, search-svc | commerce-oncall |
| search-svc | 2 | elasticsearch | platform-oncall |
| notifications-svc | 3 | kafka, email-gateway | platform-oncall |
| recommendations-svc | 3 | search-svc, feature-store | ml-oncall |

### Runbooks (10) — fields match `models.Runbook`
Keyed by service + symptom class; each has `symptoms[]`, `steps[]`, `tags[]`.
| id | service | symptom class |
|---|---|---|
| RB-001 | checkout-api | 5xx spike after deploy |
| RB-002 | payments-svc | payment gateway timeouts |
| RB-003 | cart-svc | elevated latency / redis evictions |
| RB-004 | catalog-svc | DB connection-pool exhaustion |
| RB-005 | identity-svc | auth failures / token errors |
| RB-006 | search-svc | elasticsearch red cluster |
| RB-007 | notifications-svc | kafka consumer lag |
| RB-008 | recommendations-svc | model-serving errors |
| RB-009 | (cross) | OOMKilled / memory leak |
| RB-010 | (cross) | TLS certificate expiry |

## Incident families (the recurrence that makes recall meaningful)
Target ~60 incidents total. Each family recurs several times over ~12 months with the
same `error_signature` stem and tags, so recall finds true matches. Baked MTTR trends
**down** within a family (later occurrences resolve faster — the "we've seen this
before" effect).

| family | signature stem | services | count | MTTR trend (min) |
|---|---|---|---|---|
| A deploy 5xx | `http_5xx\|deploy\|conn_pool` | checkout-api | 8 | 95 → 22 |
| B gateway timeout | `payment\|gateway_timeout\|circuit_open` | payments-svc | 7 | 140 → 35 |
| C DB pool exhaustion | `db\|too_many_connections\|pgbouncer` | catalog/identity/payments | 7 | 120 → 30 |
| D redis latency | `redis\|evictions\|latency_p99` | cart-svc | 6 | 70 → 18 |
| E es red cluster | `elasticsearch\|red\|disk_watermark` | search-svc | 6 | 160 → 45 |
| F kafka lag | `kafka\|consumer_lag\|rebalance` | notifications-svc | 5 | 80 → 25 |
| G OOMKilled | `oom\|memory_leak\|restart_loop` | various | 6 | 110 → 40 |
| H cert expiry | `tls\|cert_expired\|handshake_fail` | various | 4 | 60 → 15 |
| — one-offs / noise | unique signatures | various | ~11 | n/a |

## Demo split (`seed_role`)
- `"memory"` — ~45 resolved incidents, chronologically older; these are RETAINED into
  the bank at seed time (this is the institutional memory).
- `"queue"` — ~15 newer incidents left `open`/`triaging`; these are the alerts a judge
  triages live. Each `queue` incident deliberately belongs to a family so recall lights
  up. Keep 1–2 genuine one-offs in the queue to show honest low-confidence behavior.

## Time convention (keeps the demo "fresh")
Incidents store **`created_days_ago`** (int) and **`mttr_minutes`** (float) rather than
absolute timestamps. The seeder computes:
`created_at = now_ms() - created_days_ago*86_400_000`; for resolved incidents
`resolved_at = created_at + mttr_minutes*60_000`. Retain older→newer so the learning
curve replays in true chronological order.

## Field contract (per incident) — superset of `models.Incident`
`external_id` (INC-####), `title`, `service`, `severity` (SEV1|SEV2|SEV3), `symptom`,
`error_signature`, `tags[]`, `metrics{}` (e.g. `{"error_rate":0.18,"p99_ms":4200}`),
`log_excerpt` (2–5 realistic log lines, mono-rendered), `status`, `root_cause`,
`remediation_steps[]`, `resolver`, plus seed-only `created_days_ago`, `seed_role`, and
`family`. Resolved (`seed_role:"memory"`) incidents MUST have `root_cause`,
`remediation_steps`, `resolver`, `mttr_minutes`; `queue` incidents leave those empty.

## JSON envelope (see `data/seed_sample.json`)
```json
{
  "meta": {"dataset": "northwind-demo", "synthetic": true, "note": "SYNTHETIC demo data — not real telemetry"},
  "services": [ /* Service */ ],
  "runbooks": [ /* Runbook */ ],
  "incidents": [ /* incident objects with the fields above */ ]
}
```

## Retention document (what gets written to memory on resolve)
When a `memory` incident is seeded (and when a live incident is resolved), retain a rich
document — not just the title — so recall is high-signal. Template:
```
[INCIDENT INC-0042 · checkout-api · SEV1]
Symptom: 5xx spike after 14:02 deploy. Signature: http_5xx|deploy|conn_pool.
Root cause: new release lowered DB pool max below peak; connections exhausted.
Fix: rolled back release 2024.10.3; raised pgbouncer pool to 200; added canary gate.
Runbook: RB-001. Resolver: payments-oncall. MTTR: 22m. Outcome: resolved, no recurrence.
```
Retain as `mem_type="experience"`, `source=external_id`, `metadata={service,severity,
family,signature,mttr_minutes}`. This is the learning loop `IncidentService.resolve`
implements (see `backend/services/incidents.py`).

## Verification
`data/seed_sample.json` must parse and satisfy: every incident `service` ∈ services;
every `severity` ∈ {SEV1,SEV2,SEV3}; `memory` incidents have non-empty root_cause +
remediation_steps + mttr_minutes; at least one family has ≥2 members with a `queue`
member matching a `memory` member (so recall has a demonstrable true hit). A tiny schema
check lives in `tests/` (see TEST_PLAN.md, dataset-integrity test).
