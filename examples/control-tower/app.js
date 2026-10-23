// Mini Control Tower: a minimal SC LiveSim consumer.
// Loads open shipments once via REST, then keeps them current from the SSE stream.
"use strict";

const params = new URLSearchParams(location.search);
const API = (params.get("api") || `${location.protocol}//${location.hostname}:8000`).replace(/\/$/, "");
const MAX_ROWS = 100;
const MAX_EVENTS = 40;

const shipments = new Map(); // id -> {id, status, destination, mode, atd, eta_planned, eta_current}
let run = null;
let runFetchedAt = 0;
let delivered = 0;
let source = null;

const $ = (id) => document.getElementById(id);

function fmt(ts) {
  if (!ts) return "–";
  return new Date(ts).toISOString().slice(0, 16).replace("T", " ");
}

function destinationText(dest) {
  if (!dest) return "–";
  return [dest.city, dest.country].filter(Boolean).join(", ");
}

function cell(text, className) {
  const td = document.createElement("td");
  td.textContent = text;
  if (className) td.className = className;
  return td;
}

function fromShipment(s) {
  const attrs = (s.order && s.order.attributes) || {};
  return {
    id: s.id,
    status: s.status,
    destination: attrs.destination,
    mode: attrs.shipping_mode,
    atd: s.atd,
    eta_planned: s.eta_planned,
    eta_current: s.eta_current,
  };
}

async function api(path, options) {
  const resp = await fetch(API + path, options);
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp.json();
}

async function loadRun() {
  const runs = await api("/runs");
  if (!runs.length) return null;
  // Prefer the most recent active run, else the most recent run.
  const active = runs.filter((r) => ["created", "running", "paused"].includes(r.status));
  const candidates = active.length ? active : runs;
  return candidates[candidates.length - 1];
}

async function loadOpenShipments() {
  shipments.clear();
  for (let offset = 0; ; offset += 1000) {
    const page = await api(
      `/shipments?run=${run.id}&open=true&expand=order&limit=1000&offset=${offset}`
    );
    page.forEach((s) => shipments.set(s.id, fromShipment(s)));
    if (page.length < 1000) break;
  }
}

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

  fill($("delayed-body"), delayed.slice(0, MAX_ROWS), 6, "No delayed shipments.", (s) => {
    const days = s.eta_current && s.eta_planned
      ? Math.round((new Date(s.eta_current) - new Date(s.eta_planned)) / 864e5 * 10) / 10
      : null;
    return [
      cell(s.id, "mono"), cell(destinationText(s.destination)), cell(s.mode || "–"),
      cell(fmt(s.eta_planned), "mono"), cell(fmt(s.eta_current), "mono late"),
      cell(days === null ? "–" : `+${days} d`, "num late"),
    ];
  });
  fill($("open-body"), open, 6, "No open shipments.", (s) => [
    cell(s.id, "mono"), cell(destinationText(s.destination)), cell(s.mode || "–"),
    cell(fmt(s.atd), "mono"), cell(fmt(s.eta_planned), "mono"),
    cell(s.status.replace("_", " "), s.status === "delayed" ? "late" : ""),
  ]);
}

const flashing = new Set();

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

function logEvent(event) {
  const li = document.createElement("li");
  li.append(
    Object.assign(document.createElement("span"), { className: "ts mono", textContent: fmt(event.ts) }),
    Object.assign(document.createElement("span"), {
      className: `type${event.type === "shipment.delayed" ? " delayed" : ""}`,
      textContent: event.type,
    }),
    Object.assign(document.createElement("span"), {
      className: "mono",
      textContent: `${event.object_id} → ${destinationText(event.payload.destination)}`,
    }),
  );
  const list = $("events");
  list.prepend(li);
  while (list.children.length > MAX_EVENTS) list.lastChild.remove();
}

function applyEvent(event) {
  const p = event.payload;
  const id = event.object_id;
  if (event.type === "shipment.departed") {
    shipments.set(id, {
      id, status: p.status, destination: p.destination, mode: p.shipping_mode,
      atd: p.atd, eta_planned: p.eta_planned, eta_current: p.eta_planned,
    });
  } else if (event.type === "shipment.delayed" || event.type === "shipment.eta_changed") {
    const s = shipments.get(id);
    if (s) Object.assign(s, { status: p.status || s.status, eta_current: p.eta_current });
    flashing.add(id);
  } else if (event.type === "shipment.delivered") {
    shipments.delete(id);
    delivered += 1;
  }
  logEvent(event);
  scheduleRender();
}

function connect() {
  if (source) source.close();
  const types = "shipment.departed,shipment.delayed,shipment.eta_changed,shipment.delivered";
  source = new EventSource(`${API}/stream?run=${encodeURIComponent(run.id)}&types=${types}`);
  source.onopen = () => setConn(true);
  source.onerror = () => setConn(false); // EventSource reconnects with Last-Event-ID
  for (const type of types.split(",")) {
    source.addEventListener(type, (msg) => applyEvent(JSON.parse(msg.data)));
  }
}

function setConn(online) {
  const el = $("conn");
  el.textContent = online ? "live" : "reconnecting";
  el.className = `pill ${online ? "pill-on" : "pill-off"}`;
}

function showRun() {
  $("run-id").textContent = `${run.id} · ${run.scenario} · ${run.time_factor}×`;
  $("run-status").textContent = run.desired_status !== run.status && run.status !== "finished"
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

async function refreshRun() {
  try {
    const fresh = await api(`/runs/${run.id}`);
    run = fresh;
    runFetchedAt = Date.now();
    showRun();
  } catch (err) {
    console.warn("run refresh failed", err);
  }
}

async function start() {
  $("api-link").textContent = API;
  $("api-link").href = `${API}/docs`;
  try {
    run = await loadRun();
  } catch (err) {
    $("run-id").textContent = `API not reachable (${API})`;
    setTimeout(start, 3000);
    return;
  }
  if (!run) {
    $("run-id").textContent = "no run yet – waiting for the engine";
    setTimeout(start, 3000);
    return;
  }
  runFetchedAt = Date.now();
  showRun();
  connect(); // subscribe first, so nothing is missed while loading
  await loadOpenShipments();
  render();
  setInterval(tickClock, 250);
  setInterval(refreshRun, 5000);
  $("toggle").addEventListener("click", async () => {
    const action = run.desired_status === "paused" ? "resume" : "pause";
    run = await api(`/runs/${run.id}/${action}`, { method: "POST" });
    runFetchedAt = Date.now();
    showRun();
  });
}

start();
