# Muninn — Demo Script

A tight, judge-facing walkthrough. Target length **3 minutes**. It tells one story:
*memory turns a generic guess into a specific, cited fix — and the system visibly gets
better as it runs.*

> Run this against the completed build (Phase 7 of `BUILD_SPEC.md`). Until then, the
> steps below double as the acceptance walkthrough: each beat maps to a feature and its
> endpoint, so the demo and the build stay in lockstep. Everything runs offline; if real
> Hindsight/Groq keys are set, the badges flip to `hindsight`/`groq` and the story is
> identical.

## Before you start (30s, off-camera)

```bash
python3 -m backend.server      # http://127.0.0.1:8000
```

Open the URL. Confirm the statusbar badges are green (`api ok`, memory `local` or
`hindsight`, reasoner `local` or `groq`). Click **Seed demo data** once — the queue fills
with synthetic SEV alerts and the memory counter shows the pre-seeded incident corpus.

## The 3-minute run

**0:00 — The problem (spoken, on the console view).**
"On-call has a memory problem. The same outage recurs months apart and the next responder
starts from zero. Muninn is an incident copilot that *remembers* — it's built on
Hindsight, an agent-memory system. Watch what memory does to a live alert."

**0:20 — Pick the alert.**
Click the top SEV1 in the queue (`checkout-api — 5xx spike after deploy`). The active pane
shows the raw alert: service, severity, symptom, error signature. No answers yet.

**0:35 — Cold (memory OFF).** Flip the **Cold ⇄ Warm** toggle to **Cold** and click
**Triage**. The brief streams in: a generic, low-confidence guess — "investigate recent
changes, check logs." **No citations.** The recall pane is empty.
"That's an LLM with no institutional memory. Reasonable, but generic — it doesn't know
your systems."

**1:05 — Warm (memory ON).** Flip to **Warm** and click **Triage** again on the same alert.
Now the recall pane lights up: three past incidents (`INC-0007`, `INC-0021`, `INC-0039`),
each with a similarity score, all from the same failure family. The brief names the
**DB-pool regression**, recommends the **exact rollback + `pgbouncer` pool bump** that
worked before, and **cites those incident IDs** and runbook **`RB-001`**.
"Same model, same alert — but now grounded in what the org already learned. Root cause,
the specific fix, the runbook, who resolved it last time."

**1:45 — Why it's grounded (one line on Hindsight).**
"Under the hood, the alert's signature became a **recall** query to Hindsight; its
retrieval fused semantic, keyword, and temporal signals to surface the nearest prior
incidents. The agent cited them. Nothing is hand-waved — those citations are real
retrieved memories, shown with their scores."

**2:00 — Close the loop: resolve & remember.**
Click **Resolve & remember**. Muninn **retains** this incident as a new memory; the memory
counter ticks up by one.
"That's the compounding part. This incident is now memory for the next responder."

**2:20 — Proof it improves: Insights.**
Switch to the **Insights** view. Show **MTTR trending down** and the **learning curve**
rising — accuracy of recall improving as the bank grows.
"This isn't a mockup. The learning curve is computed by replaying real recall calls as the
memory bank grows over the labeled demo dataset. More memory, better first-response."

**2:45 — Land it.**
"Muninn: institutional memory as a first-class system. The longer it runs, the faster your
team resolves incidents. Built on Hindsight — retain, recall, reflect — as the core loop.
Thanks."

## If something fails live (graceful degradation)

- **No keys / offline:** expected — badges read `local`. The whole story works; say so.
- **Reasoner slow or errors:** the agent tolerates malformed tool calls (FR-12) and falls
  back to the deterministic local reasoner; the brief still renders.
- **Empty recall on Warm:** you picked an alert with no matching family. Use a seeded
  queue alert (`INC-0058`, `INC-0061`, `INC-0063`) which are designed to match retained
  families.

## The one-sentence version

"Muninn is an incident copilot that remembers — flip one toggle and watch a generic guess
become a cited, specific fix drawn from your org's past outages, then watch it get faster
as it learns."

## Honesty checklist for the demo

- The dataset is **synthetic and labeled** — say it once, on screen it's marked too.
- Badges show the **real** backend serving each result (`hindsight`/`local`,
  `groq`/`local`). Never claim a cloud call that didn't happen.
- MTTR and the learning curve are **computed over the demo dataset**, not illustrative
  numbers typed into a slide.
