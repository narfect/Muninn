# Muninn: the incident-response copilot that remembers

*Submission article — Hack with Hyderabad 3.0. All incident data shown is synthetic and
clearly labeled.*

## The problem nobody schedules for

At 2 a.m. an alert fires: `checkout-api` is throwing 5xx errors right after a deploy. The
engineer on call has never seen this exact failure. But someone on the team has — four
months ago, a different person rolled back a release that had quietly shrunk the database
connection pool. That knowledge exists. It's just trapped: in a resolved ticket, a chat
thread that scrolled away, and the head of a teammate who has since changed squads.

So the new responder starts from zero. They re-derive the root cause, re-discover the fix,
and mean-time-to-resolve pays for it — every single time the incident recurs.

On-call engineering doesn't have a knowledge problem. It has a **memory** problem.

## The idea: make institutional memory a first-class system

Muninn treats memory as the product, not a feature. The name comes from Norse myth —
Muninn is Odin's raven, "memory," who flies the world and returns with what he has seen.

The loop is simple and it compounds:

- **A new alert fires.** Muninn takes the incident's error signature and *recalls* the
  most similar past incidents.
- **An LLM agent drafts a triage brief** grounded in those memories — root cause, the
  exact fix that worked, the runbook, who resolved it, and the historical MTTR — and it
  **cites** the incidents it drew from.
- **When the incident is resolved,** Muninn *retains* the outcome as a new memory.

The longer it runs, the better it gets. That's the whole thesis, and it's something you
can watch happen.

## The one demo that tells the story

Muninn's interface has a single toggle: **Cold ⇄ Warm**. It triages the same live alert
twice.

**Cold** (memory off) is a capable model with no institutional context. It gives a
reasonable but generic answer — "investigate recent changes, check the logs" — with no
citations. This is the honest baseline: it's what an LLM copilot without memory actually
does.

**Warm** (memory on) is the same model, same alert — but now the recall pane lights up
with three past incidents from the same failure family, each with a similarity score. The
brief names the DB-pool regression, recommends the specific rollback plus the `pgbouncer`
pool bump that worked last time, and cites `INC-0007`, `INC-0021`, `INC-0039` and runbook
`RB-001`.

Then you click **Resolve & remember**, the memory counter ticks up, and the **Insights**
view shows MTTR trending down and a **learning curve** rising as the memory bank grows.

The before/after isn't a slide. It's the same code path with memory switched off and on.

## How Hindsight powers it

Memory is built on **Hindsight** (by Vectorize), an agent-memory system, used for its
three core operations as the heart of Muninn — not a bolt-on:

- **retain** — on resolve, store a rich *experience* memory: symptom, error signature,
  root cause, exact fix, runbook, resolver, MTTR, keyed by incident ID.
- **recall** — on a new alert, query with the incident's signature and get scored, cited
  past incidents. Hindsight's retrieval fuses semantic, keyword, graph, and temporal
  signals so the matches are relevant, not just lexically similar.
- **reflect** — synthesize cross-incident patterns ("we've seen this family repeatedly
  since June, and this fix has worked every time") into the brief.

A single memory bank isolates the incident corpus. Because the sandbox blocks external
network calls and judges may run without keys, Muninn also ships a faithful **offline
memory store** implementing the same interface with a hybrid retriever — so the concept is
fully demonstrable with zero setup. A badge in the UI always shows which backend served a
result, so a demo can never misrepresent itself.

## Engineering choices

Muninn is deliberately **zero-install**: a Python standard-library backend
(`http.server`, `sqlite3`, `urllib`) serving a dependency-free vanilla-JS console. One
command runs it, anywhere, offline. SQLite is the system of record; Hindsight is the
semantic memory.

Two swappable seams — the memory store and the reasoner — let the exact same code path run
against real cloud services (Hindsight for memory, Groq for the LLM) or fully offline with
local fallbacks. The agent is built to tolerate malformed tool calls rather than crash.
The test suite is stdlib `unittest`, offline and deterministic, with no network in unit
tests and no fabricated passes.

## Why it matters

MTTR reduction is the daily pain of every on-call team, and the cost of relearning solved
problems is enormous and invisible. Muninn attacks exactly that: it turns each resolved
incident into leverage for the next one. Memory that compounds is the difference between a
team that fights the same fire repeatedly and one that gets measurably faster over time.

## Honesty note

Everything in the demo runs over a synthetic, clearly-labeled incident dataset. The MTTR
and learning-curve numbers are computed by replaying real recall calls as the memory bank
grows — not illustrative figures. Where a real integration isn't reachable, Muninn uses
its offline fallback and says so. Nothing here reports a result the system didn't
actually produce.
