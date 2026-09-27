# Muninn — Submission Checklist

Everything needed to submit Muninn to Hack with Hyderabad 3.0, and the order to do it in.
Check each box before you hit submit.

## Repo hygiene

- [ ] Repo is public (or judges are invited) and named `muninn`.
- [ ] `README.md` renders correctly on the repo home page (tables, ASCII diagram).
- [ ] `LICENSE` present (MIT).
- [ ] `.gitignore` excludes `data/muninn.db*`, `data/memory_local.json`, `.env`,
      `__pycache__/`.
- [ ] `.env.example` committed; **no real `.env` committed** (no secrets in history).
- [ ] `requirements.txt` present (documents the stdlib-only stance + optional accelerators).

## It runs (verify locally, don't assume)

- [ ] `python3 -m backend.server` boots and serves the UI at `http://127.0.0.1:8000`.
- [ ] `make test` (or `python3 -m unittest discover -s tests`) is green.
- [ ] `make compile` passes (import/syntax gate).
- [ ] Statusbar badges show honest backends (`local`/`hindsight`, `local`/`groq`).
- [ ] Offline path works with **no** keys set.

## Documentation (all present under `docs/`)

- [ ] `SRS.md` — requirements.
- [ ] `ARCHITECTURE.md` + `diagrams/` — system design.
- [ ] `PLAN.md`, `MVP.md`, `BUILD_SPEC.md` — plan & scope.
- [ ] `HINDSIGHT.md` — the required "how memory is used" write-up.
- [ ] `DATASET.md` — synthetic dataset spec (labeled).
- [ ] `TEST_PLAN.md` — test strategy + FR coverage.
- [ ] `UI_SPEC.md` — frontend design system.
- [ ] `DEMO_SCRIPT.md` — the live walkthrough.

## Content deliverables (under `content/`)

- [ ] `ARTICLE.md` — submission article.
- [ ] `SOCIAL.md` — X thread + LinkedIn post + one-liner.
- [ ] `VIDEO_SCRIPT.md` — video shot list + VO.

## Media (produce after the Phase-7 build is verified)

- [ ] **Screenshots**: console (queue + active + memory), a Cold brief, a Warm brief with
      citations, the Insights view. Capture from the running app — don't mock them.
- [ ] **Demo video** (2–2.5 min): record against the running app following
      `VIDEO_SCRIPT.md`. Say "synthetic data" once; show the real badge.
- [ ] Add screenshots to the README (a `docs/media/` folder) and the video link to the top.

## Submission form

- [ ] Project name: **Muninn**.
- [ ] Tagline: "The incident-response copilot that remembers."
- [ ] Repo URL + demo video URL.
- [ ] Hindsight called out explicitly (the 25% criterion) — link `docs/HINDSIGHT.md`.
- [ ] Note that all data is synthetic and labeled.

## Final honesty pass

- [ ] No claim in any doc reports a result the app hasn't actually produced.
- [ ] The README "Project status" section accurately describes what's built vs. specced.
- [ ] Every metric shown anywhere is computed over the labeled dataset, not illustrative.
