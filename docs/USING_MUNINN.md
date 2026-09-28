# Using Muninn

A practical guide to running Muninn, driving the demo, and understanding how access works
in real use. For the *why* and the architecture, see [`../README.md`](../README.md) and
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## What Muninn is

Muninn is an incident-response copilot with **institutional memory**. When a new alert
fires it recalls the most similar past incidents — their root cause, the exact fix, the
runbook, the resolver, the MTTR — and an LLM agent drafts a **cited triage brief**. When
you resolve an incident, the outcome is *retained*, so the system gets better the longer
it runs. Memory is powered by **Hindsight** (retain / recall / reflect); everything runs
fully offline against a local memory store and a local reasoner when no keys are present.

All incident data shipped with the app is **synthetic and labeled as such**. Nothing in
the demo is faked at runtime: recall, brief generation, and every metric are computed for
real from the actual (synthetic) incidents. The only synthetic thing is the seed dataset.

## The one demo that tells the story — Cold ⇄ Warm

The centerpiece is a single toggle that triages the *same* live alert twice:

- **Cold (memory OFF):** the agent reasons with no institutional memory — a generic,
  low-confidence guess, no citations. This is on-call starting from zero.
- **Warm (memory ON):** the agent recalls similar past incidents and drafts a brief that
  names the real root cause, the fix that worked before, and **cites** the specific prior
  incidents (`INC-…`) and runbook it drew from.

"**Compare cold vs warm**" runs both at once and shows them side by side. Then **Resolve &
remember** writes a new memory, the memory counter ticks up, and the **Insights** view
shows MTTR by service plus a **learning curve** that rises as the bank grows.

## How to run

No API keys and no internet required.

```bash
python -m backend.server          # serves the API + UI on http://127.0.0.1:8000
```

Open <http://127.0.0.1:8000> in a browser.

> **"disk I/O error" on startup?** SQLite's write-ahead log can't be created on some
> filesystems (network shares, certain sandboxes). Point the DB at a local temp path:
>
> ```bash
> MUNINN_DB=/tmp/muninn.db python -m backend.server
> ```

## Demo mode (no login needed)

For the local, offline demo the app boots in **open demo mode**: there is no login gate,
and a labeled switcher in the status bar lets you flip between the three roles instantly —

> **Demo mode — no login needed. Viewing as:** [ Viewer ] [ Responder ] [ Admin ]

Each click mints a **real** server session for that role and re-boots the console, so the
capability gating you see is exactly what a real login of that role would get — nothing is
faked. A small **Log in** link is always available if you'd rather use a real account.

Two conveniences back this mode, both **local-demo-only** (see `.env.example`):

- **`MUNINN_DEMO_OPEN`** (default `true`) turns the switcher on and skips the login gate.
  It is **force-disabled the moment a real `MUNINN_SERVER_SECRET` is configured**, so a
  production deploy can never accidentally ship without authentication.
- **`MUNINN_DEMO_AUTOSEED`** (default `true`) loads the labeled synthetic dataset on
  startup if the incidents table is empty, so a fresh checkout is never a blank slate.
  It's a no-op once data exists or when the flag is off.

Under demo mode, three accounts are provisioned on first boot —
`admin@muninn.local`, `responder@muninn.local`, `viewer@muninn.local` — and the switcher
signs in as each. These are ordinary hashed-password accounts; the demo endpoints never
accept or return a password, they just start a session for the requested role.

## Real-use login model

Set `MUNINN_SERVER_SECRET` to a strong random value and demo mode switches off
automatically — the app then requires authentication and shows the login gate:

```bash
MUNINN_SERVER_SECRET="$(python -c 'import secrets;print(secrets.token_urlsafe(32))')" \
  python -m backend.server
```

Accounts use email + password with HttpOnly session cookies and double-submit CSRF
protection. **The first account created becomes the `admin`**; everyone who signs up after
that starts as a `viewer`, and an admin promotes them from the **Users** view.

## Roles and capabilities

Roles form a hierarchy — `viewer < responder < admin` — and each higher role includes
everything below it. The server enforces a `required_role` on every route (403 on a
too-low role, plus CSRF on mutating requests); the frontend mirrors the same map so it
never offers a control the server would reject.

| Capability | Viewer | Responder | Admin |
|---|:--:|:--:|:--:|
| View incidents, services, runbooks, metrics | ✅ | ✅ | ✅ |
| Recall memory, reflect on patterns | ✅ | ✅ | ✅ |
| Run triage (cold/warm), compare cold vs warm | ✅ | ✅ | ✅ |
| Create incidents | — | ✅ | ✅ |
| Transition / resolve incidents, give feedback | — | ✅ | ✅ |
| Seed / reset the demo dataset | — | — | ✅ |
| Manage users (view + change roles) | — | — | ✅ |

**Read-only analysis is a viewer capability on purpose:** anyone should be able to watch
the memory work — recall, the cold/warm brief, the compare — without being able to change
incident state. Only responders and admins mutate incidents; only admins seed data or
manage users.

## A 6-step walkthrough for judges

1. **Boot with an empty DB in demo mode.** `python -m backend.server`, open
   <http://127.0.0.1:8000>. No login appears; autoseed has filled the queue with labeled
   synthetic incidents, and you're viewing as **Admin**.
2. **Pick a live alert** from the queue. The right pane shows *What we've seen before* —
   scored, cited recall of similar past incidents.
3. **Toggle Cold → Warm.** Cold is a generic, uncited guess; Warm names the real root
   cause and fix and **cites** the prior `INC-…` and runbook it drew from. Or hit
   **Compare cold vs warm** to see both at once.
4. **Resolve & remember.** Fill the resolution and resolve — a new memory is retained and
   the *memories* counter in the status bar ticks up. That's the learning loop closing.
5. **Open Insights.** MTTR by service and a **learning curve** that rises as the bank
   grows — every number computed from the actual incidents, with legends beneath each
   chart and screen-reader data tables behind them.
6. **Exercise the roles.** Use the status-bar switcher: as **Viewer** you can run
   triage/compare but *New incident*, *Seed*, and *Users* are gone; as **Responder** you
   can create and resolve; as **Admin** everything returns. Then click **Log in** to see
   the real auth gate, or restart with `MUNINN_SERVER_SECRET` set to see demo mode switch
   off entirely (login required, the demo switcher gone, and the demo-login endpoint 404s).

