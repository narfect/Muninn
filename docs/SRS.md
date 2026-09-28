# Software Requirements Specification (SRS)
## Muninn — The On-Call Copilot That Never Forgets an Incident

**Version:** 1.0  ·  **Date:** 2026-09-28  ·  **Status:** Baseline for Hack with Hyderabad 3.0
**Author:** Neha (BTech CSE)  ·  **Required technology:** Hindsight (Vectorize) agent memory

---

## 1. Introduction

### 1.1 Purpose
This document specifies the requirements for **Muninn**, an AIOps incident-response
copilot whose defining capability is **institutional memory**. When a production
alert fires, Muninn recalls how *similar incidents were handled in the past* —
their root causes, the exact remediation steps that worked, the runbook that
applied, who resolved them, and how long they took — and uses an LLM to synthesize
a triage brief and a recommended fix, **with citations to the specific past
incidents it learned from**. After each incident is resolved, the outcome is written
back into memory, so the agent measurably improves over time.

### 1.2 Scope
Muninn targets the single, high-value workflow of **first-responder triage during a
live incident**. It is deliberately *not* a general chatbot. The memory layer is
provided by **Hindsight**, which is central (not incidental) to the product's value:
without memory the agent is a generic advice generator; with memory it becomes an
expert that has "seen this before."

Muninn is delivered as a self-contained web application (HTTP API + single-page UI)
with a realistic synthetic dataset of services, runbooks, and historical incidents,
plus a scripted demo that shows the learning curve in under 60 seconds.

### 1.3 Definitions
- **Incident** — a disruption to a service (e.g., elevated 5xx, latency, saturation).
- **Signature** — a compact fingerprint of an incident (service + symptom + error class).
- **Memory bank** — a Hindsight container isolating all memories for one context.
- **Retain / Recall / Reflect** — Hindsight's three core memory operations.
- **MTTR** — Mean Time To Resolve.
- **TEMPR** — Hindsight's fused retrieval (semantic + keyword + graph + temporal).
- **Cold start** — agent operating with memory disabled (baseline for before/after).

### 1.4 Stakeholders
On-call engineers / SREs (primary users), incident commanders, engineering managers
(MTTR & knowledge-retention outcomes), and — for this hackathon — the judges and
technical recruiters evaluating engineering depth.

## 2. Overall Description

### 2.1 Product perspective
Muninn sits conceptually where an alerting tool (PagerDuty, Datadog, Grafana
OnCall) hands off to a human. It ingests an alert, consults an evolving memory of
past incidents, and produces an actionable brief. The memory layer is Hindsight;
the reasoning layer is an LLM (Groq, OpenAI-compatible). Muninn owns the
orchestration, the incident lifecycle, the metrics, and the operator UI.

### 2.2 User classes
- **On-call engineer** — receives alerts, wants a fast, evidence-backed starting point.
- **Incident commander** — needs a defensible summary and history at a glance.
- **Engineering manager** — cares about MTTR trend and captured institutional knowledge.

### 2.3 Operating environment
Python 3.10+ backend (standard library HTTP server + SQLite), a dependency-free
vanilla-JS single-page UI served by the backend, and optional external services
(Hindsight Cloud or self-hosted Hindsight; Groq for the LLM). The application runs
with **zero package installation** and degrades gracefully when external services
are absent (clearly-labeled local mode).

### 2.4 Design constraints
- Hindsight must be the memory system and must be visibly central to the value.
- The application must run and demo reliably offline; simulated results must never
  be presented as real.
- Code must be clean, layered, and covered by automated tests.

### 2.5 Assumptions & dependencies
- A Hindsight instance (Cloud or local) and a Groq API key are available in
  production; when they are not, Muninn uses a local memory store and a
  deterministic local reasoner, both explicitly surfaced in the UI.

## 3. Functional Requirements

Each requirement has an ID, a priority (M=MVP, S=should, C=could) and is verifiable.

### 3.1 Incident ingestion & lifecycle
- **FR-1 (M):** The system shall accept a new alert (service, severity, symptom,
  error signature, metrics, raw log excerpt) via API and create an OPEN incident.
- **FR-2 (M):** The system shall persist incidents and their state transitions
  (OPEN → TRIAGING → MITIGATED → RESOLVED) with timestamps.
- **FR-3 (M):** The system shall compute a normalized **signature** for each incident
  used for memory retrieval and analytics.
- **FR-4 (S):** The system shall list, filter, and retrieve incidents and their full
  timeline.

### 3.2 Memory (Hindsight) — the core
- **FR-5 (M):** On incident creation, the system shall **recall** relevant past
  incidents from Hindsight for the incident's signature/description and return a
  ranked list with per-item relevance scores and provenance.
- **FR-6 (M):** The system shall **retain** every resolved incident (context,
  root cause, remediation steps, outcome) into Hindsight as a typed memory.
- **FR-7 (M):** The system shall use Hindsight **reflect** to synthesize a
  root-cause hypothesis and remediation plan grounded in recalled memories, shaped
  by an SRE "disposition."
- **FR-8 (M):** The system shall expose a **memory toggle** so the same incident can
  be triaged with memory ON vs OFF (before/after), for demonstration and evaluation.
- **FR-9 (S):** The system shall capture operator **feedback** ("root cause correct",
  "fix worked") and retain it as an observation that influences future ranking.
- **FR-10 (S):** The system shall isolate memory per bank and support seeding the
  bank from the historical dataset.

### 3.3 Reasoning agent
- **FR-11 (M):** The system shall run an LLM triage agent that can call tools
  (`recall_memory`, `reflect_memory`, `get_runbook`, `get_service`) and produce a
  structured brief: summary, likely root cause, ranked remediation steps, runbook
  reference, and cited past incidents.
- **FR-12 (M):** The agent shall **handle LLM function-calling errors** (malformed
  tool calls, unknown tools, invalid arguments) without crashing, retrying or
  degrading gracefully.
- **FR-13 (S):** The agent shall stream its output token-by-token to the UI.

### 3.4 Analytics & learning
- **FR-14 (M):** The system shall compute and expose **MTTR** overall and per service.
- **FR-15 (M):** The system shall expose a **learning curve**: retrieval quality /
  agent confidence as a function of how many incidents have been retained.
- **FR-16 (S):** The system shall report memory coverage (how many past incidents a
  new alert can draw on) and a before/after quality delta.

### 3.5 User interface
- **FR-17 (M):** A single-page operator console shall present the incident feed, a
  live triage "war room" with the streaming agent, a **memory panel** showing recalled
  incidents with similarity scores and provenance, and analytics dashboards.
- **FR-18 (M):** The UI shall provide one-click demo scenarios that fire scripted
  alerts and clearly indicate demo/local mode when external services are absent.

## 4. Non-Functional Requirements
- **NFR-1 Performance:** Recall + brief for a new alert completes in < 2 s in local
  mode; UI remains responsive during streaming.
- **NFR-2 Reliability:** No unhandled exceptions on the API; all external calls are
  wrapped with timeouts and fallbacks. The app never hard-fails because Hindsight or
  Groq is unreachable.
- **NFR-3 Security:** Input validation on every endpoint; secrets only via env vars;
  no secrets in logs or the repo; parameterized SQL; localhost binding by default.
- **NFR-4 Portability:** Runs on any machine with Python 3.10+, no package install
  required for the core.
- **NFR-5 Maintainability:** Layered architecture, typed dataclasses, docstrings,
  and automated tests covering memory, retrieval, agent, API, and metrics.
- **NFR-6 Transparency:** The active memory backend and LLM backend are always shown
  in the UI so nothing simulated is mistaken for real.
- **NFR-7 Accessibility:** Sufficient color contrast, keyboard-focusable controls,
  and semantic markup in the UI.

## 5. Data Requirements
A realistic synthetic dataset: ~8 services with dependencies, ~10 runbooks, and
~60 historical incidents spanning ~6 months, each with symptoms, real-looking error
logs and metrics, root cause, remediation steps, resolver, and MTTR. A set of
scripted demo scenarios drives the 60-second story. Data is generated
deterministically (LLM optional) so the demo is reproducible.

## 6. Verification
Every FR maps to at least one automated test or a documented demo step (see
`docs/PLAN.md` §Traceability and the `tests/` suite). Acceptance = all tests pass,
`make demo` reproduces the before/after learning story, and the UI renders the
feed, memory panel, streaming brief, and dashboards end to end.


