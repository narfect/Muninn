# Muninn — UI / Frontend Design Spec

Status: **design contract + scaffold**. This document is the source of truth for the
frontend. The design tokens, app shell, and `api` client are provided concretely in
`static/` (the "design contract layer"); the view renderers are stubs marked
`IMPLEMENT (Claude Code):`. Build the UI against this spec and the API contract in
`backend/api/routes.py`.

Dependency-free by mandate: no npm, no build step, no external CDN (sandbox blocks
egress). One `index.html`, one `styles.css`, one `app.js`, all vanilla. Fonts must
degrade to a system stack; do **not** hard-depend on a network font.

---

## 1. The subject, in one breath

Muninn is the copilot an on-call SRE opens at 3am when a service is on fire. A new
alert fires; Muninn recalls what the org already learned from past incidents — root
cause, the exact fix, the runbook, who resolved it, how long it took — and drafts a
triage brief with citations. After the incident is resolved, Muninn **remembers** the
outcome, so the next person starts from institutional memory instead of a blank page.

Audience: on-call engineers, incident commanders, SRE leads. Job: cut time-to-triage
by turning past outages into recall. Emotional register: calm, precise, high-signal —
a control room at night, not a marketing page.

---

## 2. Design plan (brainstorm → review)

### Color — "Nightwatch" console
Not near-black-plus-one-accent, and not tinted-grey chrome. A genuinely blue, slightly
desaturated deep console, with a **dual semantic accent** that encodes the product's
whole thesis: memory OFF is *cold* (steel cyan), memory ON is *warm* (violet glow).
Color is information here, not decoration.

| token | hex | role |
|---|---|---|
| `--bg` | `#0F1724` | deep ink-blue base (the room) |
| `--panel` | `#151E2E` | raised panel surface |
| `--panel-2` | `#1B2637` | inset / row hover |
| `--line` | `#27344A` | hairline borders, dividers |
| `--ink` | `#E7ECF4` | primary text (cool off-white) |
| `--muted` | `#8DA0BC` | secondary text, metadata |
| `--warm` | `#8B7BF6` | **signature** — memory ON / recall glow (violet) |
| `--warm-soft` | `#8B7BF633` | violet glow wash (33 = alpha) |
| `--cold` | `#57C3D8` | memory OFF baseline (steel cyan) |
| `--sev1` | `#FF5C77` | SEV1 critical |
| `--sev2` | `#FF9F45` | SEV2 |
| `--sev3` | `#F5D66B` | SEV3 |
| `--ok` | `#3FD8A0` | resolved / healthy |

**Review vs. tells.** The "near-black + single acid accent" tell (#2) is avoided: the
base is blue (`#0F1724`, not `#0B0B0B` chrome), and the accent system is *dual and
semantic* (warm vs cold ties directly to the memory ON/OFF demo). Severity hues encode
real data. Boldness is spent in exactly one place — the warm recall glow — everything
else stays quiet.

### Type — machine vs. human
Two roles, one honest distinction:
- **Machine/telemetry** (error signatures, log lines, service IDs, metrics, the
  wordmark): a **monospace** stack. This is meaningful, not a label-decoration tell —
  mono gives column alignment and char disambiguation for data an engineer must read
  precisely.
- **Human prose** (headings, briefs, copy): a clean **grotesque/system-sans** stack.

Stacks (no network dependency):
```
--mono: ui-monospace, "JetBrains Mono", "Cascadia Code", "SF Mono", Menlo, Consolas, monospace;
--sans: system-ui, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
```
Optional (document only, do not require): Claude Code MAY self-host *Space Grotesk* for
display headings if the font files are committed to `static/fonts/` — never load it
from a CDN. Type scale (1.250 major third): 12 / 13 / 15(base) / 18 / 22 / 28 / 36.
Body line-length ≤ 72ch. Do **not** use ALL-CAPS tracked eyebrows; do **not** accent a
single word in a heading; do **not** append "→" to buttons.

### Layout — three-pane war-room

The console is the product, so the console *is* the page — no marketing shell. A slim
status bar on top; three panes below; a second "Insights" view for the dashboards.

```
┌───────────────────────────────────────────────────────────────────────────┐
│  ᛗ muninn   memory: hindsight ●  llm: groq ●  57 memories      [Insights ▸] │  status bar
├───────────────┬───────────────────────────────────────┬─────────────────────┤
│  INCIDENTS    │   ACTIVE INCIDENT                      │  WHAT WE'VE SEEN     │
│  (queue)      │                                        │  BEFORE (memory)     │
│               │   checkout-api · SEV1 · 12m ago        │                      │
│  ▸ SEV1  ...  │   "5xx spike after deploy"             │  ▸ 0.91  inc-0042    │  ← recall
│    SEV2  ...  │   ┌──────────── triage ─────────────┐  │        deploy rollback│    glows
│    SEV2  ...  │   │  [ Cold ⇄ Warm ]  ← the hero    │  │  ▸ 0.86  inc-0031    │    when
│    SEV3  ...  │   │  streaming brief w/ citations    │  │        conn-pool      │    Warm
│               │   │  root cause · steps · runbook    │  │  ▸ 0.71  inc-0018    │      │
│  [+ new / seed]│  └──────────────────────────────────┘ │  provenance + score  │
│               │   [ Resolve & remember ]               │  [ reflect ]         │
└───────────────┴───────────────────────────────────────┴─────────────────────┘
```

Alignment: everything left-aligned (an ops tool reads like a console log). The queue is
a **dense row list**, not cards. The center brief is the focal reading surface. The
memory pane is where Hindsight is made visible — it carries the signature violet glow.
Distinct treatment per hierarchy; avoid the "identical rounded cards, one shadow" kit.

Mobile (≤ 820px): panes stack vertically as tabs — [ Incident | Brief | Memory ] —
the status bar collapses to the wordmark + a health dot.

### Principles (what makes this page itself)
1. **The hero is the Cold ⇄ Warm split**, not a big number. Toggling it re-runs triage
   and shows, side by side, the same alert triaged without memory vs. with it. This is
   Muninn's entire thesis, made interactive — the first thing a judge should touch.
2. **Recall is a visible, cited object.** Every warm brief links its claims to specific
   past incident IDs in the memory pane; clicking a citation highlights its source.
3. **The learning loop closes on screen.** "Resolve & remember" visibly writes a new
   memory; the memory counter in the status bar ticks up.
4. Color encodes state (warm/cold/severity), never mood.

---

## 3. Views

### 3.1 Console (default)
The three-pane workspace above. State: selected incident, `use_memory` toggle, current
brief, current recall, streaming status.

### 3.2 Insights
MTTR and the **learning curve** — recall quality rising as the bank grows. Two charts
(hand-drawn with `<canvas>` or inline SVG; no chart library — none is installable).
Copy frames it honestly: metrics computed over the labeled demo dataset.

### 3.3 First-run / empty
No incidents yet → an invitation, not a void: "Muninn's memory is empty. Seed the demo
dataset to watch it recall past outages." with a single **Seed demo data** action.

---

## 4. Signature interaction — the recall reveal

When the user flips to **Warm**, one orchestrated moment (not scattered effects):
1. the memory pane populates with matched incidents, each with a similarity score;
2. as the brief streams, citation chips (`inc-0042`) light in `--warm`;
3. hovering a citation raises its source row in the memory pane (and vice versa).

Motion is limited to this reveal and to action feedback (resolve, seed). Respect
`prefers-reduced-motion`: replace transitions with instant state changes. No page-load
animation, no per-section fade-slide, no hover-lift on every element.

---

## 5. Component inventory → API binding

Each component names the endpoint that feeds it (contract in `backend/api/routes.py`).

| component | endpoint(s) | notes |
|---|---|---|
| status bar health | `GET /api/health` | backend dots + `n_memories` count |
| incident queue | `GET /api/incidents[?status=]` | dense rows, severity color chip |
| new incident | `POST /api/incidents` | form: service, severity, symptom, log excerpt |
| seed / reset | `POST /api/demo/seed`, `POST /api/demo/reset` | label clearly as demo data |
| active incident header | `GET /api/incidents/{id}` | + timeline |
| triage brief (non-stream) | `POST /api/triage` | `{incident_id, use_memory}` |
| triage brief (stream) | `GET /api/triage/stream?incident_id=&use_memory=` | SSE tokens → brief |
| Cold ⇄ Warm split | `POST /api/compare` | `{incident_id}` → `{cold, warm}` |
| memory pane (recall) | `POST /api/memory/recall` or `recall` in triage resp | scores + provenance |
| reflect | `POST /api/memory/reflect` | agentic reasoning over the bank |
| resolve & remember | `POST /api/incidents/{id}/resolve` | writes a new memory |
| feedback thumbs | `POST /api/incidents/{id}/feedback` | reinforces memory |
| MTTR chart | `GET /api/metrics/mttr` | overall + by service |
| learning-curve chart | `GET /api/metrics/learning-curve` | series for line chart |
| insights summary | `GET /api/metrics/summary` | counts by severity/service |

---

## 6. Copy deck (ops voice: plain, active, sentence case)

- Wordmark: `ᛗ muninn` (rune mark optional; ASCII "muninn" is fine).
- Memory toggle: `Cold` / `Warm` with sublabel "no memory" / "with memory".
- Primary triage action: **Run triage**. Streaming state: "Recalling past incidents…"
  then "Drafting brief…".
- Recall pane title: **What we've seen before**. Empty: "No prior incidents match this
  signature yet." Score label: "match".
- Resolve action: **Resolve & remember**. Success toast: "Saved. Muninn will remember
  this." (memory counter increments).
- Reflect action: **Reflect** — sublabel "ask memory for patterns".
- Seed: **Seed demo data**. Reset: **Reset demo**. Both tagged with a small "demo"
  marker so simulated data is never mistaken for real (NFR transparency).
- Errors, interface voice, never apologetic-vague: "Triage failed — the reasoner didn't
  respond. Retry, or run in Cold mode." "Couldn't reach the memory backend; showing the
  offline store."
- Backend badges: `hindsight` / `local` for memory, `groq` / `local` for llm, so the
  demo is always honest about which path is live.

---

## 7. Accessibility & quality floor (non-negotiable)
- Contrast ≥ 4.5:1 for body text on `--bg`/`--panel` (palette is tuned for this).
- Visible keyboard focus ring (`--warm` outline); full keyboard nav of queue + toggle.
- `prefers-reduced-motion` respected. `prefers-color-scheme` not required (dark-first).
- Semantic landmarks (`header`/`main`/`aside`), `aria-live="polite"` on the streaming
  brief and the toast region, `aria-pressed` on the Cold/Warm toggle.
- Responsive to 360px. No layout depends on hover alone.

---

## 8. Scaffold provided in `static/`
- `index.html` — app shell: status bar, three-pane grid, Insights section, mount points
  (`#queue`, `#active`, `#memory`, `#insights`), and the toast/live regions. Loads
  `styles.css` + `app.js`. Runs today (served by `backend/server.py`) and shows the
  empty-state invitation.
- `styles.css` — **concrete design contract**: all tokens as CSS custom properties,
  reset, typography scale, the three-pane grid, status bar, and severity/warm/cold
  utility classes. Component-level styling is marked for Claude Code.
- `app.js` — **concrete contract**: `api` fetch helper (JSON + SSE), app state object,
  a tiny hash view-router, and health-badge boot. Render functions are stubs marked
  `IMPLEMENT (Claude Code):` with the exact endpoint each should call.

Build order for Claude Code: health badges → queue + seed → triage (non-stream) →
memory pane + citations → Cold/Warm compare → streaming → resolve+remember → Insights.
