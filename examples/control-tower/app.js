// Mini Control Tower: a minimal SC LiveSim consumer.
// Loads current state via REST, then keeps shipments current from the SSE stream.
// Inventory and disruptions are refreshed every few seconds.
"use strict";

const params = new URLSearchParams(location.search);
const API = (params.get("api") || `${location.protocol}//${location.hostname}:8000`).replace(/\/$/, "");
const MAX_ROWS = 100;
const MAX_EVENTS = 40;
const STREAM_TYPES = [
  "shipment.departed", "shipment.delayed", "shipment.eta_changed", "shipment.delivered",
  "disruption.started", "disruption.ended", "inventory.below_safety_stock",
];

const shipments = new Map(); // id -> row
const safetyStock = new Map(); // "loc:item" -> safety stock (learned from alerts)
const names = { locations: new Map(), items: new Map() };
let run = null;
let runFetchedAt = 0;
let delivered = 0;
let source = null;
let generation = 0; // increments on run switch; stale async work is ignored

const $ = (id) => document.getElementById(id);

const fmt = (ts) => (ts ? new Date(ts).toISOString().slice(0, 16).replace("T", " ") : "–");
const place = (p) => (p ? [p.city, p.country].filter(Boolean).join(", ") : "–");
const locName = (id) => names.locations.get(id) || id;
const itemName = (id) => names.items.get(id) || id;

function route(s) {
  return s.origin ? `${place(s.origin)} → ${place(s.destination)}` : place(s.destination);
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

async function api(path, options) {
  const resp = await fetch(API + path, options);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

function fromShipment(s) {
  const attrs = (s.order && s.order.attributes) || {};
  return {
    id: s.id, status: s.status, atd: s.atd, eta_planned: s.eta_planned, eta_current: s.eta_current,
    direction: s.order ? s.order.direction : "outbound",
    origin: attrs.origin, destination: attrs.destination, mode: attrs.shipping_mode, cause: null,
  };
}

async function loadOpenShipments(gen) {
  const fresh = new Map();
  for (let offset = 0; ; offset += 1000) {
    const page = await api(`/shipments?run=${run.id}&open=true&expand=order&limit=1000&offset=${offset}`);
    page.forEach((s) => fresh.set(s.id, fromShipment(s)));
    if (page.length < 1000) break;
  }
  if (gen !== generation) return;
  for (const [id, s] of fresh) if (!shipments.has(id)) shipments.set(id, s);
}

// --- rendering -------------------------------------------------------------

function fill(tbody, rows, columns, emptyText, toCells) {
  const fragment = document.createDocumentFragment();
  if (!rows.length) {
    const tr = document.createElement("tr");
    tr.className = "empty";
    const td = cell(emptyText);
    td.colSpan = columns;
    tr.append(td);
    fragment.append(tr);
  }
  for (const row of rows) {
    const tr = document.createElement("tr");
    if (flashing.has(row.id)) tr.className = "flash";
    tr.append(...toCells(row));
    fragment.append(tr);
  }
  tbody.replaceChildren(fragment);
}

const flashing = new Set();
const dir = (s) => (s.direction === "inbound" ? "in" : "out");

function render() {
  const all = [...shipments.values()];
  const delayed = all.filter((s) => s.status === "delayed")
    .sort((a, b) => (a.eta_planned < b.eta_planned ? -1 : 1));
  const open = all.sort((a, b) => (a.atd > b.atd ? -1 : 1)).slice(0, MAX_ROWS);

  $("kpi-open").textContent = all.length;
  $("kpi-delayed").textContent = delayed.length;
  $("kpi-share").textContent = all.length ? `${Math.round((100 * delayed.length) / all.length)} %` : "–";
  $("kpi-delivered").textContent = delivered;
  $("delayed-count").textContent = `(${delayed.length})`;
  $("open-count").textContent = `(${all.length})`;

  fill($("delayed-body"), delayed.slice(0, MAX_ROWS), 8, "No delayed shipments.", (s) => {
    const days = s.eta_current && s.eta_planned
      ? Math.round(((new Date(s.eta_current) - new Date(s.eta_planned)) / 864e5) * 10) / 10
      : null;
    return [
      cell(s.id, "mono"), cell(dir(s)), cell(route(s)), cell(s.mode || "–"),
      cell(fmt(s.eta_planned), "mono"), cell(fmt(s.eta_current), "mono late"),
      cell(days === null ? "–" : `+${days} d`, "num late"), cell(s.cause || "–", "mono"),
    ];
  });
  fill($("open-body"), open, 7, "No open shipments.", (s) => [
    cell(s.id, "mono"), cell(dir(s)), cell(route(s)), cell(s.mode || "–"),
    cell(fmt(s.atd), "mono"), cell(fmt(s.eta_planned), "mono"),
    cell(s.status.replace("_", " "), s.status === "delayed" ? "late" : ""),
  ]);
}

let renderQueued = false;
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    render();
    flashing.clear();
  });
}

function renderInventory(rows) {
  $("inventory-panel").hidden = rows.length === 0;
  $("source-note").textContent = rows.length
    ? "Showing the 100 most recent."
    : "Showing the 100 most recent. This run has no inventory data (replayed dataset).";
  fill($("inventory-body"), rows, 6, "", (r) => {
    const safety = safetyStock.get(`${r.location_id}:${r.item_id}`);
    const low = safety !== undefined && Number(r.on_hand) < safety;
    return [
      cell(locName(r.location_id)), cell(itemName(r.item_id)),
      cell(Math.round(r.on_hand), "num"), cell(Math.round(r.reserved), "num"),
      cell(Math.round(r.in_transit), "num"),
      cell(low ? `below safety (${safety})` : "ok", low ? "late" : "ok"),
    ];
  });
}

function renderDisruptions(rows) {
  const active = rows.filter((d) => !d.end);
  $("kpi-disruptions").textContent = active.length;
  $("disruption-panel").hidden = active.length === 0;
  const list = $("disruptions");
  list.replaceChildren(...active.map((d) => {
    const li = document.createElement("li");
    const targets = [...d.affected.lanes, ...d.affected.locations.map(locName)].join(", ");
    li.append(
      Object.assign(document.createElement("strong"), { textContent: d.type.replace("_", " ") }),
      document.createTextNode(` ${d.id} · severity ${d.severity} · since ${fmt(d.start)} · ${targets}`),
    );
    return li;
  }));
}

function logEvent(event) {
  const li = document.createElement("li");
  const p = event.payload;
  const subject = event.object_type === "shipment"
    ? `${event.object_id} → ${place(p.destination)}`
    : event.object_type === "inventory"
      ? `${itemName(p.item_id)} at ${locName(p.location_id)}`
      : `${event.object_id} (${p.type || ""})`;
  li.append(
    Object.assign(document.createElement("span"), { className: "ts mono", textContent: fmt(event.ts) }),
    Object.assign(document.createElement("span"), {
      className: `type${/delayed|disruption|below/.test(event.type) ? " delayed" : ""}`,
      textContent: event.type,
    }),
    Object.assign(document.createElement("span"), { className: "mono", textContent: subject }),
  );
  const list = $("events");
  list.prepend(li);
  while (list.children.length > MAX_EVENTS) list.lastChild.remove();
}

// --- live updates ------------------------------------------------------------

function applyEvent(event) {
  const p = event.payload;
  const id = event.object_id;
  if (event.type === "shipment.departed") {
    shipments.set(id, {
      id, status: p.status, direction: p.direction || "outbound", origin: p.origin,
      destination: p.destination, mode: p.shipping_mode, atd: p.atd,
      eta_planned: p.eta_planned, eta_current: p.eta_planned, cause: null,
    });
  } else if (event.type === "shipment.delayed" || event.type === "shipment.eta_changed") {
    const s = shipments.get(id);
    if (s) Object.assign(s, { status: p.status || s.status, eta_current: p.eta_current, cause: p.cause || s.cause });
    flashing.add(id);
  } else if (event.type === "shipment.delivered") {
    if (shipments.delete(id)) delivered += 1;
  } else if (event.type === "inventory.below_safety_stock") {
    safetyStock.set(`${p.location_id}:${p.item_id}`, p.safety_stock);
    refreshSide();
  } else if (event.type.startsWith("disruption.")) {
    refreshSide();
  }
  logEvent(event);
  scheduleRender();
}

function connect() {
  if (source) source.close();
  source = new EventSource(`${API}/stream?run=${encodeURIComponent(run.id)}&types=${STREAM_TYPES.join(",")}`);
  source.onopen = () => setConn(true);
  source.onerror = () => setConn(false); // EventSource reconnects with Last-Event-ID
  for (const type of STREAM_TYPES) {
    source.addEventListener(type, (msg) => applyEvent(JSON.parse(msg.data)));
  }
}

function setConn(online) {
  $("conn").textContent = online ? "live" : "reconnecting";
  $("conn").className = `pill ${online ? "pill-on" : "pill-off"}`;
}

async function refreshSide() {
  if (!run) return;
  const gen = generation;
  try {
    const [inventory, disruptions] = await Promise.all([
      api(`/inventory?run=${run.id}&limit=1000`), api(`/disruptions?run=${run.id}`),
    ]);
    if (gen !== generation) return;
    renderInventory(inventory);
    renderDisruptions(disruptions);
  } catch (err) {
    console.warn("refresh failed", err);
  }
}

// --- run selection and control ------------------------------------------------

function showRun() {
  $("run-status").textContent = run.desired_status !== run.status && !["finished", "failed"].includes(run.status)
    ? `${run.status} → ${run.desired_status}` : run.status;
  const toggle = $("toggle");
  toggle.disabled = !["running", "paused"].includes(run.status);
  toggle.textContent = run.desired_status === "paused" ? "Resume" : "Pause";
}

function tickClock() {
  if (!run) return;
  let sim = new Date(run.sim_time).getTime();
  if (run.status === "running") sim += (Date.now() - runFetchedAt) * run.time_factor;
  $("sim-time").textContent = new Date(sim).toISOString().slice(0, 19).replace("T", " ");
}

async function refreshRuns() {
  const runs = await api("/runs");
  const select = $("run-select");
  const current = run && run.id;
  select.replaceChildren(...runs.map((r) => {
    const option = new Option(`${r.id} · ${r.scenario} · ${r.time_factor}×`, r.id);
    option.selected = r.id === current;
    return option;
  }));
  if (run) {
    const fresh = runs.find((r) => r.id === run.id);
    if (fresh) {
      run = fresh;
      runFetchedAt = Date.now();
      showRun();
    }
  }
  return runs;
}

async function selectRun(runId) {
  generation += 1;
  const gen = generation;
  const runs = await api("/runs");
  run = runs.find((r) => r.id === runId);
  runFetchedAt = Date.now();
  shipments.clear();
  safetyStock.clear();
  delivered = 0;
  $("events").replaceChildren();
  showRun();
  connect(); // subscribe first, so nothing is missed while loading
  const alerts = await api(`/events?run=${run.id}&type=inventory.below_safety_stock&limit=1000`);
  alerts.forEach((e) => safetyStock.set(`${e.payload.location_id}:${e.payload.item_id}`, e.payload.safety_stock));
  await loadOpenShipments(gen);
  if (gen !== generation) return;
  render();
  refreshSide();
  params.set("run", run.id);
  history.replaceState(null, "", `?${params}`);
}

async function start() {
  $("api-link").textContent = API;
  $("api-link").href = `${API}/docs`;
  let runs;
  try {
    runs = await refreshRuns();
  } catch (err) {
    $("run-status").textContent = `API not reachable (${API})`;
    setTimeout(start, 3000);
    return;
  }
  if (!runs.length) {
    $("run-status").textContent = "no run yet – waiting for the engine";
    setTimeout(start, 3000);
    return;
  }
  const [locations, items] = await Promise.all([api("/locations"), api("/items")]);
  locations.forEach((l) => names.locations.set(l.id, l.name));
  items.forEach((i) => names.items.set(i.id, i.name));

  const wanted = params.get("run");
  const active = runs.filter((r) => ["created", "running", "paused"].includes(r.status));
  const initial = runs.find((r) => r.id === wanted) || active[active.length - 1] || runs[runs.length - 1];
  await selectRun(initial.id);
  await refreshRuns();

  $("run-select").addEventListener("change", (e) => selectRun(e.target.value));
  $("toggle").addEventListener("click", async () => {
    const action = run.desired_status === "paused" ? "resume" : "pause";
    run = await api(`/runs/${run.id}/${action}`, { method: "POST" });
    runFetchedAt = Date.now();
    showRun();
  });
  setInterval(tickClock, 250);
  setInterval(() => refreshRuns().catch(() => {}), 5000);
  setInterval(refreshSide, 3000);
}

start();
