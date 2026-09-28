/* Muninn frontend — vanilla JS (no framework, no build step, no CDN).
   api client + app state + hash router + renderers for the three-pane console and
   the insights dashboard. Everything degrades honestly if a backend is unreachable. */

"use strict";

/* ---- api client (concrete) ------------------------------------------------ */
const api = {
  // Mutating requests echo the JS-readable muninn_csrf cookie as X-CSRF-Token
  // (double-submit). The HttpOnly session cookie rides along via fetch's default
  // same-origin credentials — do NOT set credentials:"include".
  _mutHeaders() {
    const h = { "Content-Type": "application/json", "Accept": "application/json" };
    const csrf = window.Auth && Auth.csrf();
    if (csrf) h["X-CSRF-Token"] = csrf;
    return h;
  },
  async get(path) {
    const r = await fetch(path, { headers: { "Accept": "application/json" } });
    return this._json(r);
  },
  async post(path, body) {
    const r = await fetch(path, {
      method: "POST", headers: this._mutHeaders(), body: JSON.stringify(body || {}),
    });
    return this._json(r);
  },
  async patch(path, body) {
    const r = await fetch(path, {
      method: "PATCH", headers: this._mutHeaders(), body: JSON.stringify(body || {}),
    });
    return this._json(r);
  },
  async _json(r) {
    let data = null;
    try { data = await r.json(); } catch (_) { /* empty/non-json */ }
    if (!r.ok) {
      // A 401 here means the session lapsed mid-app (boot uses auth.js's own fetch,
      // so this never fires during the login gate) — bounce back to the auth screen.
      if (r.status === 401 && window.Auth) Auth.onUnauthorized();
      const msg = (data && (data.message || data.error)) || `HTTP ${r.status}`;
      const err = new Error(msg); err.status = r.status; err.data = data; throw err;
    }
    return data;
  },
  /* SSE for GET /api/triage/stream. Server emits named events (token/done/error).
     Returns the EventSource so callers can close it. */
  stream(path, { onToken, onDone, onError } = {}) {
    const es = new EventSource(path);
    es.addEventListener("token", (e) => {
      if (!onToken) return;
      try { onToken(JSON.parse(e.data).t); } catch (_) { onToken(e.data); }
    });
    es.addEventListener("done", (e) => {
      es.close();
      if (!onDone) return;
      try { onDone(JSON.parse(e.data)); } catch (_) { onDone(null); }
    });
    es.addEventListener("error", (e) => {
      es.close();
      if (onError) onError(e && e.data ? e.data : e);
    });
    return es;
  },
};

/* ---- app state ------------------------------------------------------------ */
const state = {
  incidents: [],
  selectedId: null,
  useMemory: true,   // Cold(false) <-> Warm(true) — the hero toggle
  brief: null,
  recall: null,
  health: null,
};

/* ---- tiny helpers --------------------------------------------------------- */
const $ = (sel) => document.querySelector(sel);
const el = (tag, cls, text) => { const n = document.createElement(tag); if (cls) n.className = cls; if (text != null) n.textContent = text; return n; };
let _toastTimer = null;
function toast(msg) {
  const t = $("#toast"); t.textContent = msg; t.hidden = false;
  clearTimeout(_toastTimer); _toastTimer = setTimeout(() => (t.hidden = true), 3200);
}
const sevNum = (sev) => ({ SEV1: 1, SEV2: 2, SEV3: 3 }[sev] || 3);
const pct = (x) => `${Math.round((Number(x) || 0) * 100)}%`;
function ago(ms) {
  if (!ms) return "";
  const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 90) return `${Math.round(s)}s ago`;
  const m = s / 60; if (m < 90) return `${Math.round(m)}m ago`;
  const h = m / 60; if (h < 36) return `${Math.round(h)}h ago`;
  return `${Math.round(h / 24)}d ago`;
}
function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

/* ---- health boot (concrete) ---------------------------------------------- */
async function bootHealth() {
  const wrap = $("#health");
  const set = (role, text, live) => {
    const b = wrap.querySelector(`[data-role="${role}"]`);
    if (!b) return;
    b.querySelector("b").textContent = text;
    b.classList.toggle("is-live", live === true);
    b.classList.toggle("is-local", live === false);
  };
  try {
    const h = await api.get("/api/health");
    state.health = h;
    set("memory", h.memory_backend || "?", (h.memory_backend === "hindsight"));
    set("llm", h.llm_backend || "?", (h.llm_backend === "groq"));
    set("count", String(h.n_memories ?? "—"));
  } catch (e) {
    // health endpoint not implemented yet (501) or backend down — stay honest, not broken
    set("memory", "offline"); set("llm", "offline"); set("count", "—");
  }
}

/* ---- view router (concrete) ---------------------------------------------- */
function router() {
  let view = (location.hash.replace(/^#\//, "") || "console");
  // Users is admin-only — never route a non-admin into it (server 403s anyway).
  if (view === "users" && !(window.Auth && Auth.can("users"))) view = "console";
  const isInsights = view === "insights";
  const isUsers = view === "users";
  const isConsole = !isInsights && !isUsers;
  $("#view-console").hidden = !isConsole;
  $("#view-insights").hidden = !isInsights;
  $("#view-users").hidden = !isUsers;
  document.querySelectorAll(".viewlink").forEach((a) => {
    a.setAttribute("aria-current", a.dataset.view === view ? "page" : "false");
  });
  if (isInsights) renderInsights();
  if (isUsers) renderUsers();
}

/* ---- render functions ----------------------------------------------------- */
// renderQueue(): GET /api/incidents -> fill #queue with .q-row rows; click selects.
async function renderQueue() {
  const box = $("#queue");
  try {
    const data = await api.get("/api/incidents");
    state.incidents = data.incidents || [];
  } catch (e) {
    clear(box); box.appendChild(el("p", "q-empty", "Queue unavailable.")); return;
  }
  clear(box);
  if (!state.incidents.length) {
    box.appendChild(el("p", "q-empty", "No incidents yet — seed the demo dataset."));
    return;
  }
  // open incidents first, then most-recent
  const rows = [...state.incidents].sort((a, b) =>
    (a.status === "resolved") - (b.status === "resolved") || b.created_at - a.created_at);
  for (const inc of rows) {
    const row = el("div", "q-row");
    row.setAttribute("role", "button");
    row.setAttribute("tabindex", "0");
    row.setAttribute("aria-selected", String(inc.id === state.selectedId));
    row.dataset.id = inc.id;
    row.appendChild(el("span", `q-sev sev-${sevNum(inc.severity)}`));
    const mid = el("div", "q-mid");
    mid.appendChild(el("div", "q-title", inc.title));
    mid.appendChild(el("div", "q-svc", `${inc.external_id} · ${inc.service}`));
    row.appendChild(mid);
    const right = el("div", "q-right");
    right.appendChild(el("div", "q-age", ago(inc.created_at)));
    if (inc.status === "resolved") right.appendChild(el("span", "q-badge resolved", "resolved"));
    row.appendChild(right);
    const pick = () => selectIncident(inc.id);
    row.addEventListener("click", pick);
    row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } });
    box.appendChild(row);
  }
}

async function selectIncident(id) {
  state.selectedId = id;
  state.brief = null; state.recall = null;
  document.querySelectorAll(".q-row").forEach((r) =>
    r.setAttribute("aria-selected", String(Number(r.dataset.id) === id)));
  await renderActive();
  renderRecall(null);
}

// renderActive(): GET /api/incidents/{id}; render header + Cold/Warm toggle + brief mount.
async function renderActive() {
  const host = $("#active");
  if (!state.selectedId) return;
  let inc, timeline = [];
  try {
    const data = await api.get(`/api/incidents/${state.selectedId}`);
    inc = data.incident; timeline = data.timeline || [];
  } catch (e) { toast(`Could not load incident: ${e.message}`); return; }
  state.active = inc;
  host.className = "active";
  clear(host);

  const head = el("div", "incident-head");
  const h = el("h2", null, inc.title);
  head.appendChild(h);
  head.appendChild(el("div", "incident-meta",
    `${inc.external_id} · ${inc.service} · ${inc.severity} · ${inc.status}`));
  head.appendChild(el("div", "sig mono", inc.error_signature || inc.symptom));
  host.appendChild(head);

  // HERO toggle: Cold (memory OFF) <-> Warm (memory ON)
  const toggle = el("div", "toggle");
  toggle.setAttribute("role", "group");
  toggle.setAttribute("aria-label", "Memory mode");
  const mk = (mode, label, sub) => {
    const b = el("button", null); b.dataset.mode = mode;
    b.setAttribute("aria-pressed", String((mode === "warm") === state.useMemory));
    b.appendChild(el("span", null, label));
    b.appendChild(el("span", "sub", sub));
    b.addEventListener("click", () => {
      state.useMemory = (mode === "warm");
      toggle.querySelectorAll("button").forEach((x) =>
        x.setAttribute("aria-pressed", String(x.dataset.mode === mode)));
    });
    return b;
  };
  toggle.appendChild(mk("cold", "Cold", "memory OFF"));
  toggle.appendChild(mk("warm", "Warm", "memory ON"));

  const actions = el("div", "active-actions");
  actions.appendChild(toggle);
  if (window.Auth && Auth.can("triage")) {
    const run = el("button", "btn", "Run triage");
    run.addEventListener("click", runTriage);
    actions.appendChild(run);
    const cmp = el("button", "btn btn-ghost", "Compare cold vs warm");
    cmp.addEventListener("click", renderCompare);
    actions.appendChild(cmp);
  } else {
    actions.appendChild(el("span", "role-note", "Read-only — responders can run triage."));
  }
  host.appendChild(actions);

  const mount = el("div", "brief-mount"); mount.id = "brief-mount";
  host.appendChild(mount);

  if (inc.status === "resolved") {
    host.appendChild(renderResolution(inc));
  } else if (window.Auth && Auth.can("mutate")) {
    host.appendChild(renderResolveForm(inc));
  }

  if (timeline.length) {
    const tl = el("div", "timeline");
    tl.appendChild(el("h3", "tl-head", "Timeline"));
    for (const ev of timeline) {
      const item = el("div", "tl-row");
      item.appendChild(el("span", "tl-kind mono", ev.kind));
      item.appendChild(el("span", "tl-msg", ev.message || ""));
      tl.appendChild(item);
    }
    host.appendChild(tl);
  }
}

// runTriage(): POST /api/triage {incident_id, use_memory} -> renderBrief + renderRecall.
async function runTriage() {
  if (!state.selectedId) return;
  const mount = $("#brief-mount");
  if (mount) { clear(mount); mount.appendChild(el("p", "thinking", "Muninn is thinking…")); }
  try {
    const out = await api.post("/api/triage",
      { incident_id: state.selectedId, use_memory: state.useMemory });
    state.brief = out.brief; state.recall = out.recall;
    renderBrief(out.brief);
    renderRecall(out.recall);
  } catch (e) {
    if (mount) { clear(mount); mount.appendChild(el("p", "thinking", `Triage failed: ${e.message}`)); }
  }
}

// renderBrief(brief): root cause, steps, runbook, citation chips linked to recall rows.
function renderBrief(brief, mountSel) {
  const mount = mountSel ? $(mountSel) : $("#brief-mount");
  if (!mount) return;
  clear(mount);
  if (!brief) return;
  const card = el("div", `brief ${brief.memory_used ? "is-warm" : "is-cold"}`);

  const top = el("div", "brief-top");
  top.appendChild(el("span", "brief-mode", brief.memory_used ? "WARM · memory on" : "COLD · memory off"));
  top.appendChild(el("span", "brief-conf mono", `confidence ${pct(brief.confidence)}`));
  card.appendChild(top);

  card.appendChild(el("p", "brief-summary", brief.summary || "—"));

  const rc = el("div", "brief-rc");
  rc.appendChild(el("h3", null, "Root-cause hypothesis"));
  rc.appendChild(el("p", null, brief.root_cause_hypothesis || "insufficient signal"));
  card.appendChild(rc);

  if ((brief.remediation_steps || []).length) {
    const steps = el("div", "brief-steps");
    steps.appendChild(el("h3", null, "Recommended remediation"));
    const ol = el("ol");
    for (const s of brief.remediation_steps) ol.appendChild(el("li", null, s));
    steps.appendChild(ol);
    card.appendChild(steps);
  }

  if (brief.runbook_ref) {
    const rb = el("div", "brief-rb");
    rb.appendChild(el("span", "label", "Runbook: "));
    rb.appendChild(el("span", "mono", brief.runbook_ref));
    card.appendChild(rb);
  }

  if ((brief.citations || []).length) {
    const cites = el("div", "brief-cites");
    cites.appendChild(el("span", "label", "Citations: "));
    for (const c of brief.citations) {
      const chip = el("button", "cite", `${c.id}${c.score != null ? " · " + pct(c.score) : ""}`);
      chip.title = c.excerpt || "recalled incident";
      chip.addEventListener("click", () => highlightRecall(c.id));
      cites.appendChild(chip);
    }
    card.appendChild(cites);
  } else if (!brief.memory_used) {
    card.appendChild(el("p", "brief-nocite", "No citations — memory is OFF (cold start)."));
  }

  if (brief.reflection) {
    const ref = el("div", "brief-reflection");
    ref.appendChild(el("h3", null, "Cross-incident reflection"));
    ref.appendChild(el("p", null, brief.reflection));
    card.appendChild(ref);
  }

  const prov = el("div", "brief-prov mono");
  prov.textContent = `memory: ${brief.memory_backend} · llm: ${brief.llm_backend} · ${brief.latency_ms}ms`;
  card.appendChild(prov);

  mount.appendChild(card);
}

// renderRecall(recall): recalled incidents w/ scores into #memory; hit-highlight.
function renderRecall(recall) {
  const box = $("#memory");
  clear(box);
  const mems = (recall && recall.memories) || [];
  if (!mems.length) {
    box.appendChild(el("p", "recall-empty",
      recall ? "No prior memories matched (cold start)." :
        "Run triage to recall what Muninn has seen before."));
    return;
  }
  for (const m of mems) {
    const row = el("div", `recall-row ${m.score >= 0.35 ? "is-hit" : ""}`);
    row.dataset.source = m.source || "";
    const head = el("div", "recall-head");
    head.appendChild(el("span", "score mono", pct(m.score)));
    head.appendChild(el("span", "provenance", `${m.source || "?"} · ${m.type}`));
    row.appendChild(head);
    row.appendChild(el("p", "recall-content", (m.content || "").slice(0, 320)));
    box.appendChild(row);
  }
}

function highlightRecall(source) {
  document.querySelectorAll(".recall-row").forEach((r) => {
    const on = r.dataset.source === source;
    r.classList.toggle("is-focus", on);
    if (on) r.scrollIntoView({ block: "nearest", behavior: "smooth" });
  });
}

// renderCompare(): POST /api/compare -> cold vs warm briefs side by side (centerpiece).
async function renderCompare() {
  if (!state.selectedId) return;
  const mount = $("#brief-mount");
  if (mount) { clear(mount); mount.appendChild(el("p", "thinking", "Running both cold and warm…")); }
  try {
    const out = await api.post("/api/compare", { incident_id: state.selectedId });
    clear(mount);
    const split = el("div", "compare");
    const coldCol = el("div", "compare-col"); coldCol.id = "cmp-cold";
    const warmCol = el("div", "compare-col"); warmCol.id = "cmp-warm";
    split.appendChild(coldCol); split.appendChild(warmCol);
    mount.appendChild(split);
    renderBrief(out.cold, "#cmp-cold");
    renderBrief(out.warm, "#cmp-warm");
    // recall pane reflects the warm run
    const rc = await api.post("/api/memory/recall",
      { query: state.active ? `${state.active.service}: ${state.active.symptom}` : "", top_k: 5 })
      .catch(() => null);
    renderRecall(rc);
  } catch (e) {
    if (mount) { clear(mount); mount.appendChild(el("p", "thinking", `Compare failed: ${e.message}`)); }
  }
}

function renderResolveForm(inc) {
  const form = el("form", "resolve-form");
  form.appendChild(el("h3", null, "Resolve & remember"));
  const rc = el("input"); rc.name = "root_cause"; rc.placeholder = "Root cause"; rc.required = true;
  const steps = el("input"); steps.name = "steps"; steps.placeholder = "Remediation steps (comma-separated)";
  const who = el("input"); who.name = "resolver"; who.placeholder = "Resolver";
  [rc, steps, who].forEach((i) => { i.className = "field"; form.appendChild(i); });
  const submit = el("button", "btn", "Resolve & retain to memory");
  submit.type = "submit";
  form.appendChild(submit);
  form.addEventListener("submit", (e) => { e.preventDefault(); resolveAndRemember(inc.id, {
    root_cause: rc.value.trim(),
    remediation_steps: steps.value.split(",").map((s) => s.trim()).filter(Boolean),
    resolver: who.value.trim() || "oncall",
  }); });
  return form;
}

function renderResolution(inc) {
  const box = el("div", "resolution");
  box.appendChild(el("h3", null, "Resolution"));
  box.appendChild(el("p", "res-rc", inc.root_cause || "—"));
  if ((inc.remediation_steps || []).length) {
    const ol = el("ol");
    for (const s of inc.remediation_steps) ol.appendChild(el("li", null, s));
    box.appendChild(ol);
  }
  const meta = el("div", "res-meta mono");
  meta.textContent = `resolver: ${inc.resolver || "—"}` +
    (inc.mttr_minutes != null ? ` · MTTR ${inc.mttr_minutes}m` : "");
  box.appendChild(meta);
  box.appendChild(el("p", "res-note", "✓ Retained to memory — future incidents will recall this."));
  return box;
}

// resolveAndRemember(): POST /api/incidents/{id}/resolve -> toast + bump memory count.
async function resolveAndRemember(id, payload) {
  if (!payload.root_cause) { toast("Root cause is required."); return; }
  try {
    await api.post(`/api/incidents/${id}/resolve`, payload);
    toast("Resolved — retained to memory.");
    await renderQueue();
    await renderActive();
    bootHealth();
  } catch (e) { toast(`Resolve failed: ${e.message}`); }
}

// renderInsights(): GET /api/metrics/summary -> stat cards + MTTR + learning-curve charts.
async function renderInsights() {
  const host = $("#insights");
  clear(host);
  let s;
  try { s = await api.get("/api/metrics/summary"); }
  catch (e) { host.appendChild(el("p", null, `Metrics unavailable: ${e.message}`)); return; }

  const stats = el("div", "stat-cards");
  const card = (label, val) => { const c = el("div", "stat"); c.appendChild(el("div", "stat-val mono", String(val))); c.appendChild(el("div", "stat-label", label)); return c; };
  stats.appendChild(card("mean MTTR (min)", (s.mttr && s.mttr.overall_min) || 0));
  stats.appendChild(card("incidents", s.n_incidents || 0));
  stats.appendChild(card("resolved", s.n_resolved || 0));
  stats.appendChild(card("memories", s.n_memories || 0));
  host.appendChild(stats);

  const grid = el("div", "chart-grid");
  const mttrCard = el("div", "chart-card");
  mttrCard.appendChild(el("h3", null, "MTTR by service (min)"));
  const c1 = el("canvas"); c1.width = 520; c1.height = 260; mttrCard.appendChild(c1);
  grid.appendChild(mttrCard);

  const lcCard = el("div", "chart-card");
  lcCard.appendChild(el("h3", null, "Learning curve — recall quality as memory grows"));
  const c2 = el("canvas"); c2.width = 520; c2.height = 260; lcCard.appendChild(c2);
  grid.appendChild(lcCard);
  host.appendChild(grid);

  drawBarChart(c1, (s.mttr && s.mttr.by_service) || {});
  drawLearningCurve(c2, s.learning_curve || []);
}

// seedDemo(): POST /api/demo/seed -> reload queue + health. resetDemo(): POST /api/demo/reset.
async function seedDemo() {
  try {
    const r = await api.post("/api/demo/seed");
    toast(`Seeded ${r.seeded.incidents} synthetic incidents.`);
    await renderQueue();
    bootHealth();
  } catch (e) { toast(`Seed failed: ${e.message}`); }
}

/* ---- canvas charts (no chart lib) ----------------------------------------- */
const CHART = { ink: "#E7ECF4", muted: "#8DA0BC", line: "#27344A", warm: "#8B7BF6", cold: "#57C3D8" };

function drawBarChart(canvas, byService) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height, pad = 34;
  ctx.clearRect(0, 0, W, H);
  const entries = Object.entries(byService).sort((a, b) => b[1] - a[1]).slice(0, 8);
  if (!entries.length) { _emptyChart(ctx, W, H); return; }
  const max = Math.max(...entries.map((e) => e[1]), 1);
  const bw = (W - pad * 2) / entries.length;
  ctx.strokeStyle = CHART.line; ctx.beginPath();
  ctx.moveTo(pad, H - pad); ctx.lineTo(W - pad, H - pad); ctx.stroke();
  entries.forEach(([svc, val], i) => {
    const h = (val / max) * (H - pad * 2);
    const x = pad + i * bw + bw * 0.15, w = bw * 0.7, y = H - pad - h;
    ctx.fillStyle = CHART.warm; ctx.fillRect(x, y, w, h);
    ctx.fillStyle = CHART.ink; ctx.font = "11px ui-monospace, monospace"; ctx.textAlign = "center";
    ctx.fillText(String(val), x + w / 2, y - 4);
    ctx.fillStyle = CHART.muted;
    ctx.fillText(svc.length > 10 ? svc.slice(0, 9) + "…" : svc, x + w / 2, H - pad + 14);
  });
}

function drawLearningCurve(canvas, series) {
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height, pad = 34;
  ctx.clearRect(0, 0, W, H);
  if (!series.length) { _emptyChart(ctx, W, H); return; }
  // axes
  ctx.strokeStyle = CHART.line; ctx.beginPath();
  ctx.moveTo(pad, pad); ctx.lineTo(pad, H - pad); ctx.lineTo(W - pad, H - pad); ctx.stroke();
  const n = series.length;
  const X = (i) => pad + (n === 1 ? 0 : (i / (n - 1)) * (W - pad * 2));
  const Y = (v) => H - pad - v * (H - pad * 2);   // v in [0,1]
  const plot = (key, color) => {
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.beginPath();
    series.forEach((p, i) => {
      const v = Math.max(0, Math.min(1, Number(p[key]) || 0));
      i === 0 ? ctx.moveTo(X(i), Y(v)) : ctx.lineTo(X(i), Y(v));
    });
    ctx.stroke();
    ctx.fillStyle = color;
    series.forEach((p, i) => {
      const v = Math.max(0, Math.min(1, Number(p[key]) || 0));
      ctx.beginPath(); ctx.arc(X(i), Y(v), 2.5, 0, Math.PI * 2); ctx.fill();
    });
  };
  plot("coverage", CHART.cold);
  plot("avg_top_score", CHART.warm);
  // legend
  ctx.font = "11px ui-monospace, monospace"; ctx.textAlign = "left";
  ctx.fillStyle = CHART.warm; ctx.fillText("● top match score", pad + 6, pad + 4);
  ctx.fillStyle = CHART.cold; ctx.fillText("● cumulative coverage", pad + 6, pad + 18);
}

function _emptyChart(ctx, W, H) {
  ctx.fillStyle = CHART.muted; ctx.font = "13px system-ui"; ctx.textAlign = "center";
  ctx.fillText("Seed the demo dataset to populate metrics.", W / 2, H / 2);
}

/* ---- new-incident form ---------------------------------------------------- */
async function newIncident() {
  const title = prompt("Incident title?");
  if (!title) return;
  const service = prompt("Service?", "checkout-api");
  if (!service) return;
  const severity = (prompt("Severity (SEV1/SEV2/SEV3)?", "SEV1") || "SEV1").toUpperCase();
  const symptom = prompt("Symptom?", title) || title;
  try {
    const r = await api.post("/api/incidents", { title, service, severity, symptom });
    toast(`Created ${r.incident.external_id}.`);
    await renderQueue();
    selectIncident(r.incident.id);
  } catch (e) { toast(`Create failed: ${e.message}`); }
}

/* ---- users view (admin-only role management) ------------------------------ */
// renderUsers(): GET /api/users -> table with a role <select> + Save per row.
// PATCH /api/users/{id} {role}. Editing your own row is disabled (changing your
// own role invalidates your session server-side — avoid locking yourself out).
async function renderUsers() {
  const host = $("#users-table");
  clear(host);
  if (!(window.Auth && Auth.can("users"))) {
    host.appendChild(el("p", null, "Admins only.")); return;
  }
  let users;
  try { users = (await api.get("/api/users")).users || []; }
  catch (e) { host.appendChild(el("p", null, `Users unavailable: ${e.message}`)); return; }
  if (!users.length) { host.appendChild(el("p", null, "No users.")); return; }

  const me = Auth.user();
  const table = el("table", "users-table");
  const thead = el("thead"), htr = el("tr");
  ["Name", "Email", "Role", ""].forEach((h) => htr.appendChild(el("th", null, h)));
  thead.appendChild(htr); table.appendChild(thead);
  const tbody = el("tbody");
  for (const u of users) {
    const isSelf = me && u.id === me.id;
    const tr = el("tr");
    tr.appendChild(el("td", null, u.name || "—"));
    tr.appendChild(el("td", "mono", u.email));
    const sel = el("select", "field role-select");
    ["viewer", "responder", "admin"].forEach((r) => {
      const opt = el("option", null, r); opt.value = r;
      if (u.role === r) opt.selected = true; sel.appendChild(opt);
    });
    sel.disabled = !!isSelf;
    const roleTd = el("td"); roleTd.appendChild(sel); tr.appendChild(roleTd);
    const save = el("button", "btn btn-ghost", "Save"); save.disabled = !!isSelf;
    save.addEventListener("click", async () => {
      _busy(save, true);
      try {
        await api.patch(`/api/users/${u.id}`, { role: sel.value });
        toast(`Updated ${u.email} → ${sel.value}.`);
      } catch (e) { toast(`Update failed: ${e.message}`); }
      _busy(save, false, "Save");
    });
    const actTd = el("td"); actTd.appendChild(isSelf ? el("span", "role-note", "you") : save);
    tr.appendChild(actTd);
    tbody.appendChild(tr);
  }
  table.appendChild(tbody); host.appendChild(table);
}
function _busy(btn, on, label) { btn.disabled = on; if (label != null) btn.textContent = label; }

/* ---- user chip + role gating --------------------------------------------- */
function renderUserChip(user) {
  const chip = $("#user-chip");
  if (!chip || !user) return;
  clear(chip); chip.hidden = false;
  const btn = el("button", "chip-btn");
  btn.setAttribute("aria-haspopup", "menu");
  btn.setAttribute("aria-expanded", "false");
  btn.appendChild(el("span", "chip-name", user.name || user.email));
  btn.appendChild(el("span", `role-badge role-${user.role}`, user.role));
  const menu = el("div", "chip-menu"); menu.hidden = true;
  menu.setAttribute("role", "menu");
  const out = el("button", "chip-item", "Sign out");
  out.setAttribute("role", "menuitem");
  out.addEventListener("click", () => Auth.logout());
  menu.appendChild(out);
  const setOpen = (open) => { menu.hidden = !open; btn.setAttribute("aria-expanded", String(open)); };
  btn.addEventListener("click", (e) => { e.stopPropagation(); setOpen(menu.hidden); });
  document.addEventListener("click", () => { if (!menu.hidden) setOpen(false); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !menu.hidden) setOpen(false); });
  chip.appendChild(btn); chip.appendChild(menu);
}

// Hide controls the current role can't use (the server still enforces; this just
// keeps the UI from offering a button that would 403). Mirrors the route table.
function applyRoleGating() {
  const A = window.Auth || { can: () => false };
  const canSeed = A.can("seed");     // demo/seed + demo/reset = admin
  const canMutate = A.can("mutate"); // create incident = responder
  const canUsers = A.can("users");   // user administration = admin
  ["btn-seed", "btn-seed-2"].forEach((id) => { const b = document.getElementById(id); if (b) b.hidden = !canSeed; });
  const nb = $("#btn-new"); if (nb) nb.hidden = !canMutate;
  const nu = $("#nav-users"); if (nu) nu.hidden = !canUsers;
}

/* ---- wire + boot ---------------------------------------------------------- */
let _wired = false;
function wire() {
  if (_wired) return; _wired = true;
  ["btn-seed", "btn-seed-2"].forEach((id) => { const b = document.getElementById(id); if (b) b.addEventListener("click", seedDemo); });
  const nb = $("#btn-new"); if (nb) nb.addEventListener("click", newIncident);
  window.addEventListener("hashchange", router);
}

// Boot callback — runs only AFTER Auth.require() confirms a live session, so no
// app-data request ever fires while unauthenticated.
function boot(user) {
  renderUserChip(user);
  applyRoleGating();
  router();
  bootHealth();
  renderQueue();
}

document.addEventListener("DOMContentLoaded", () => {
  wire();
  if (window.Auth) {
    Auth.onAuthenticated(boot);
    Auth.require();   // 200 -> boot(user); 401 -> auth screen, no data fetched
  } else {
    // auth.js failed to load — fail safe to the plain app rather than a blank page
    router(); bootHealth(); renderQueue();
  }
});
