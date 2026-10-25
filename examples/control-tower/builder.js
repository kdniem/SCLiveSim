// Scenario Builder: choose industry, region, size, flows and disruptions,
// preview the generated network, then save it and start a run.
"use strict";

const params = new URLSearchParams(location.search);
const API = (params.get("api") || `${location.protocol}//${location.hostname}:8000`).replace(/\/$/, "");
const $ = (id) => document.getElementById(id);
const form = $("form");
let options = null;
let lastSpec = null;
let previewTimer = null;

async function api(path, init) {
  const resp = await fetch(API + path, init);
  const text = await resp.text();
  const body = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const detail = body && body.detail;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail || resp.status));
  }
  return body;
}

function card(group, value, title, text, checked) {
  const label = document.createElement("label");
  label.className = "card";
  const input = Object.assign(document.createElement("input"), { type: "radio", name: group, value, checked });
  const strong = Object.assign(document.createElement("strong"), { textContent: title });
  const span = Object.assign(document.createElement("span"), { textContent: text });
  label.append(input, strong, span);
  return label;
}

function segment(group, value, text, checked) {
  const label = document.createElement("label");
  const input = Object.assign(document.createElement("input"), { type: "radio", name: group, value, checked });
  label.append(input, Object.assign(document.createElement("span"), { textContent: text }));
  return label;
}

function request() {
  const data = new FormData(form);
  const body = {
    industry: data.get("industry"),
    region: data.get("region"),
    size: data.get("size"),
    inbound: form.inbound.checked,
    production: form.production.checked,
    disruptions: data.get("disruptions"),
    demand_level: Number(data.get("demand_level")),
    horizon_days: Number(data.get("horizon_days")),
    seed: Number(data.get("seed") || 0),
  };
  const name = (data.get("name") || "").trim();
  if (name) body.name = name;
  return body;
}

function industry() {
  return options.industries.find((i) => i.name === form.industry.value);
}

function syncControls() {
  const ind = industry();
  form.production.disabled = !ind.produces;
  $("production-note").textContent = ind.produces ? "" : "(not used in this industry)";
  $("demand-out").textContent = `${Number(form.demand_level.value).toFixed(1)}×`;
}

function schedulePreview() {
  syncControls();
  clearTimeout(previewTimer);
  previewTimer = setTimeout(preview, 250);
}

async function preview() {
  try {
    const result = await api("/builder/preview", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(request()),
    });
    lastSpec = result.spec;
    renderPreview(result.summary, result.spec);
    setMessage("message", "");
  } catch (err) {
    setMessage("message", err.message, true);
  }
}

function renderPreview(summary, spec) {
  $("description").textContent = summary.description;
  const figures = [
    ["Sites", (summary.locations.warehouse || 0) + (summary.locations.plant || 0)],
    ["Customers", summary.locations.customer_site || 0],
    ["Suppliers", summary.locations.supplier_site || 0],
    ["Items", summary.items],
    ["Lanes", Object.entries(summary.lanes_by_mode).map(([m, n]) => `${n} ${m}`).join(", ")],
    ["Orders per day", summary.expected_orders_per_day],
    ["Production lines", summary.production_lines],
    ["Horizon", `${summary.horizon_days} days`],
  ];
  $("figures").replaceChildren(...figures.flatMap(([k, v]) => [
    Object.assign(document.createElement("dt"), { textContent: k }),
    Object.assign(document.createElement("dd"), { textContent: v }),
  ]));
  const list = $("disruption-list");
  list.replaceChildren(...(summary.disruptions.length ? summary.disruptions.map((d) => {
    const li = document.createElement("li");
    li.append(Object.assign(document.createElement("strong"), { textContent: d.type.replace("_", " ") }),
      document.createTextNode(` day ${Math.round(d.start_day)} for ${d.duration_days} days, severity ${d.severity}`));
    return li;
  }) : [Object.assign(document.createElement("li"), { textContent: "None" })]));
  drawMap(spec);
}

// Equirectangular map of the network, scaled to its bounding box.
function drawMap(spec) {
  const W = 640, H = 340, PAD = 24;
  // Networks spanning the Pacific (e.g. Americas + Asia) are drawn across it.
  const rawLons = spec.locations.map((l) => l.lon);
  const wrap = Math.max(...rawLons) - Math.min(...rawLons) > 180;
  const shifted = spec.locations.map((l) => ({ ...l, lon: wrap && l.lon < 0 ? l.lon + 360 : l.lon }));
  const locs = new Map(shifted.map((l) => [l.id, l]));
  const lats = shifted.map((l) => l.lat), lons = shifted.map((l) => l.lon);
  const [minLat, maxLat, minLon, maxLon] = [Math.min(...lats), Math.max(...lats), Math.min(...lons), Math.max(...lons)];
  const LABEL = 110; // room for site labels on the right
  const sx = (W - 2 * PAD - LABEL) / Math.max(maxLon - minLon, 1), sy = (H - 2 * PAD) / Math.max(maxLat - minLat, 1);
  const s = Math.min(sx, sy);
  const ox = (W - LABEL - (maxLon - minLon) * s) / 2, oy = (H - (maxLat - minLat) * s) / 2;
  const x = (l) => (ox + (l.lon - minLon) * s).toFixed(1);
  const y = (l) => (oy + (maxLat - l.lat) * s).toFixed(1);
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  for (const lane of spec.lanes) {
    const a = locs.get(lane.origin_location_id), b = locs.get(lane.destination_location_id);
    const line = document.createElementNS(NS, "line");
    Object.entries({ x1: x(a), y1: y(a), x2: x(b), y2: y(b), class: `lane lane-${lane.mode}` })
      .forEach(([k, v]) => line.setAttribute(k, v));
    const title = document.createElementNS(NS, "title");
    title.textContent = `${lane.id}: ${lane.mode}, ${lane.planned_transit_days} days`;
    line.append(title);
    svg.append(line);
  }
  for (const l of shifted) {
    const kind = l.type === "customer_site" ? "cus" : l.type === "supplier_site" ? "sup" : "site";
    const dot = document.createElementNS(NS, "circle");
    Object.entries({ cx: x(l), cy: y(l), r: kind === "site" ? 7 : 4.5, class: `node node-${kind}` })
      .forEach(([k, v]) => dot.setAttribute(k, v));
    const title = document.createElementNS(NS, "title");
    title.textContent = `${l.name} (${l.country})`;
    dot.append(title);
    svg.append(dot);
    if (kind === "site") {
      const text = document.createElementNS(NS, "text");
      Object.entries({ x: Number(x(l)) + 10, y: Number(y(l)) + 4, class: "node-label" })
        .forEach(([k, v]) => text.setAttribute(k, v));
      text.textContent = l.name;
      svg.append(text);
    }
  }
  $("map").replaceChildren(svg);
}

function setMessage(id, text, error) {
  $(id).textContent = text;
  $(id).className = `message${error ? " error" : ""}`;
}

async function save() {
  const created = await api("/scenarios", {
    method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(request()),
  });
  const link = $("download");
  link.href = `${API}/scenarios/${encodeURIComponent(created.name)}?format=yaml`;
  link.hidden = false;
  return created;
}

async function onSave(startRun) {
  try {
    const created = await save();
    if (!startRun) {
      setMessage("message", `Saved as “${created.name}”. Start it any time from the API or here.`);
      return;
    }
    const run = await api("/runs", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ scenario: created.name, time_factor: 1440 }),
    });
    setMessage("message", `Started ${run.id}. Opening the control tower …`);
    openRun(run);
  } catch (err) {
    setMessage("message", err.message.includes("already exists")
      ? `${err.message}. Change the name or the seed.` : err.message, true);
  }
}

async function onUpload(event) {
  const file = event.target.files[0];
  if (!file) return;
  try {
    const created = await api("/scenarios/upload", {
      method: "POST", headers: { "content-type": "application/yaml" }, body: await file.text(),
    });
    setMessage("upload-message", `Uploaded “${created.name}”. Start it with POST /runs or from the control tower API.`);
  } catch (err) {
    setMessage("upload-message", err.message, true);
  }
}

function openRun(run) {
  const target = new URLSearchParams({ run: run.id });
  if (params.get("api")) target.set("api", params.get("api"));
  setTimeout(() => { location.href = `index.html?${target}`; }, 800);
}

async function onContinue() {
  setMessage("continue-message", "Starting … (the engine loads the dataset on first use)");
  try {
    const run = await api("/runs", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ scenario: `${$("dataset").value}-continued`, time_factor: 1440 }),
    });
    setMessage("continue-message", `Started ${run.id}. Opening the control tower …`);
    openRun(run);
  } catch (err) {
    setMessage("continue-message", err.message, true);
  }
}

async function onCalibrate() {
  const windowDays = Number($("window-days").value);
  const name = `${$("dataset").value}-calibrated-${windowDays}d`;
  setMessage("continue-message", "Calibrating … (about 15 seconds the first time)");
  try {
    const created = await api("/scenarios/calibrate", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ source: $("dataset").value, name, window_days: windowDays }),
    });
    const link = $("calibrated-download");
    link.href = `${API}/scenarios/${encodeURIComponent(created.name)}?format=yaml`;
    link.hidden = false;
    setMessage("continue-message", `Saved “${created.name}”. ${created.description}`);
  } catch (err) {
    setMessage("continue-message", err.message, true);
  }
}

async function start() {
  $("api-link").textContent = API;
  $("api-link").href = `${API}/docs`;
  try {
    options = await api("/builder/options");
  } catch (err) {
    setMessage("message", `API not reachable (${API}).`, true);
    setTimeout(start, 3000);
    return;
  }
  const d = options.defaults;
  $("industries").replaceChildren(...options.industries.map((i) =>
    card("industry", i.name, i.label, `${i.description}${i.produces ? " · production" : ""}`, i.name === d.industry)));
  $("regions").replaceChildren(...options.regions.map((r) =>
    card("region", r.name, r.label, r.hubs.slice(0, 3).join(", ") + " …", r.name === d.region)));
  $("sizes").replaceChildren(...Object.entries(options.sizes).map(([name, s]) =>
    segment("size", name, `${name} · ${s.sites} site${s.sites > 1 ? "s" : ""}, ${s.customers} customers`, name === d.size)));
  $("disruptions").replaceChildren(...Object.entries(options.disruption_profiles).map(([name, p]) =>
    segment("disruptions", name, `${name}${p.per_year ? ` · ~${p.per_year}/year` : ""}`, name === d.disruptions)));
  form.addEventListener("input", schedulePreview);
  form.addEventListener("change", schedulePreview);
  form.addEventListener("submit", (e) => e.preventDefault());
  $("save").addEventListener("click", () => onSave(false));
  $("start").addEventListener("click", () => onSave(true));
  $("upload").addEventListener("change", onUpload);
  $("continue").addEventListener("click", onContinue);
  $("calibrate").addEventListener("click", onCalibrate);
  schedulePreview();
}

start();
