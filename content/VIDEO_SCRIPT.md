# Muninn — Video Script (submission demo)

**Target length:** 2:00–2:30. **Format:** screen recording of the running app + voiceover.
**Tone:** calm, confident, senior-SRE. **Rule:** every on-screen result is real (offline
backend is fine) over the synthetic, labeled dataset — no fake numbers, no staged
citations. This script is the narration + shot list; the live click-path is
`docs/DEMO_SCRIPT.md`.

---

### Shot 1 — Hook (0:00–0:15)
**On screen:** Muninn console, queue of SEV alerts, statusbar badges green.
**VO:** "It's 2 a.m. and `checkout-api` is throwing 5xx errors after a deploy. The engineer
on call has never seen this. But someone on the team fixed it months ago — and that
knowledge is gone. On-call doesn't have a knowledge problem. It has a *memory* problem."

**Lower-third text:** `Muninn — the incident copilot that remembers`

---

### Shot 2 — The alert (0:15–0:30)
**On screen:** click the top SEV1; active pane shows service, severity, symptom, signature.
**VO:** "This is Muninn. Pick a live alert and you see the raw signal — service, severity,
the error signature. No answers yet. Let's see what memory does."

---

### Shot 3 — Cold (0:30–0:55)
**On screen:** toggle set to **Cold**; click **Triage**; brief streams in, recall pane empty.
**VO:** "Memory off. This is a capable model with no institutional context. The advice is
reasonable but generic — 'investigate recent changes, check the logs' — and there are no
citations. This is the honest baseline: a copilot without memory."

**Lower-third text:** `Cold = memory OFF · no citations`

---

### Shot 4 — Warm (0:55–1:30)
**On screen:** flip to **Warm**; click **Triage** on the same alert; recall pane lights up
with three scored past incidents; brief names root cause + exact fix + cites IDs + runbook.
**VO:** "Now memory on — same model, same alert. The recall pane surfaces three past
incidents from the same failure family, each with a similarity score. The brief names the
database-pool regression, recommends the exact rollback and `pgbouncer` bump that worked
last time, and *cites* the incidents it learned from, plus the runbook."

**Lower-third text:** `Warm = memory ON · cites INC-0007, INC-0021, INC-0039 · RB-001`

---

### Shot 5 — How (1:30–1:50)
**On screen:** brief close-up on the citations / recall scores; optional small callout of
the memory badge.
**VO:** "Under the hood, the alert's signature became a **recall** query to Hindsight, the
agent-memory system. Its retrieval fused semantic, keyword, and temporal signals to find
the nearest prior incidents — and the agent cited them. Those are real retrieved memories,
shown with their scores."

**Lower-third text:** `Powered by Hindsight — retain · recall · reflect`

---

### Shot 6 — Close the loop (1:50–2:05)
**On screen:** click **Resolve & remember**; memory counter increments.
**VO:** "Resolve it, and Muninn retains the outcome as a new memory. This incident is now
context for the next responder. That's the part that compounds."

---

### Shot 7 — Proof it improves (2:05–2:25)
**On screen:** switch to **Insights**; MTTR trend down, learning curve rising.
**VO:** "And it's measurable. MTTR trends down and the learning curve rises as the memory
bank grows — computed by replaying real recall calls over the labeled demo data, not typed
into a slide."

**Lower-third text:** `MTTR ↓ · learning curve ↑ (synthetic, labeled data)`

---

### Shot 8 — Land it (2:25–2:30)
**On screen:** back to the console; Muninn wordmark.
**VO:** "Muninn — institutional memory as a first-class system. The longer it runs, the
faster your team resolves. Thanks."

---

## Production notes

- **Capture at the real speed** of the offline backend; if a stream is slow, trim in edit
  rather than fake it.
- **Do not** overlay any metric that the app didn't compute on screen.
- Keep the **Cold → Warm** cut tight and side-by-side if possible — that contrast is the
  entire pitch.
- Say the word "synthetic" at least once, and let the on-screen `synthetic data` label be
  visible.
- If recording without keys, the badges read `local` — that's fine and on-message
  (runs anywhere, offline). Don't claim a cloud call you didn't make.
