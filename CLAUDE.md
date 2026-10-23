# CLAUDE.md – SC LiveSim

> Working title. Open-source engine that streams **live supply chain data** (orders, shipments, inventory, disruptions) through standard interfaces, so supply chain apps (control towers, risk tools, network optimizers) can plug in instead of loading static CSVs.

## Why this exists

Supply chain tools are usually tested against static datasets. Tools that react to change (alerts, risk scores, ETA tracking, live dashboards) need a data source that keeps moving. SC LiveSim provides that: one open schema, one event stream, many consumers.

Primary user right now: the maintainer, as a test data source for his own vibe-coded apps (first consumer: a supply chain control tower / risk tool with inbound and outbound tracking). Secondary: other developers, research and teaching. **Everything is built public, licensed and documented from the first commit.**

## Core principles (do not violate without an ADR)

1. **Open source by design.** Repo is public from day one. Code, docs and examples are written for strangers, in English.
2. **Schema first.** The core schema is the most important artifact. It is versioned separately (SemVer) and published as JSON Schema. Industry specifics go into an `attributes` JSONB field, not new tables.
3. **One-way flow.** Inputs → Engine → Postgres → Access layer → Apps. Apps never write to the database directly; they read and control runs only through the access layer.
4. **Reproducible.** Same scenario version + same seed + same engine version = identical event stream. Every export carries these as metadata.
5. **No hard cloud dependency.** Everything runs locally with `docker compose up`. Supabase may host a demo, but nothing may require it.
6. **Small and working beats big and flexible.** Stick to the current phase.

## Architecture

```
Inputs (datasets, scenario YAML, own data)
  → Engine (Python): Replay | Simulation | Calibration
  → PostgreSQL: core schema + append-only event log
  → Access layer (FastAPI): REST, SSE stream, webhooks, MCP server, Python SDK
  → Consumers: control tower, risk tool, optimizers, notebooks
```

All three modes write into the same schema and event stream. A consumer cannot tell whether data is replayed, simulated or calibrated.

Shared run controls (all modes): time factor (1x real time up to e.g. 1440x), seed, pause/resume, reset to a point in time, manually injected disruptions.

## Repository layout

```
sc-livesim/
  schema/        Core schema: Pydantic models → JSON Schema + SQL migrations
  engine/        replay/, simulation/ (later), calibration/ (later)
  scenarios/     YAML templates, one folder each with README (Phase 2)
  adapters/      Importers mapping public datasets to the core schema
  api/           FastAPI app: REST, SSE stream, (later) webhooks
  mcp_server/    MCP server (not `mcp/`: that would shadow the MCP SDK package)
  sdk/python/    Client library (Phase 3)
  examples/      Mini control tower, notebook
  docs/          MkDocs site, docs/adr/ for architecture decisions
  data/          gitignored; raw downloads go to data/raw/
  docker-compose.yml
```

## Core schema v0.1

| Area | Entity | Key fields |
| --- | --- | --- |
| Master | `location` | id, type (plant, warehouse, port, customer_site, supplier_site), name, country, lat, lon, attributes |
| Master | `partner` | id, role (supplier, customer, carrier), location_id, attributes |
| Master | `item` | id, name, category, unit, weight_kg, value, attributes |
| Master | `lane` | id, origin_location_id, destination_location_id, mode (road, sea, air, rail), planned_transit_days, cost |
| Movement | `order` | id, direction (inbound, outbound), partner_id, order_date, requested_date, status, attributes |
| Movement | `order_line` | id, order_id, item_id, quantity, value |
| Movement | `shipment` | id, order_id, lane_id, carrier_id, etd, eta_planned, eta_current, atd, ata, status |
| Movement | `inventory` | location_id, item_id, on_hand, reserved, in_transit, as_of |
| Events | `event` | id, sim_run_id, ts (simulated time), type, object_type, object_id, payload (JSONB) |
| Events | `disruption` | id, sim_run_id, type, affected (locations/lanes), start, end, severity |
| Run | `sim_run` | id, mode, scenario, scenario_version, seed, time_factor, sim_start, wall_start, status |

Event types (initial): `order.created`, `order.confirmed`, `shipment.picked_up`, `shipment.departed`, `shipment.eta_changed`, `shipment.delayed`, `shipment.delivered`, `inventory.below_safety_stock`, `disruption.started`, `disruption.ended`.

Rules:
- `event` is **append-only**. Current state (open shipments, stock) is derived from events (materialized tables or views updated by the engine in the same transaction).
- All timestamps are simulated time, stored as `timestamptz` in UTC. `sim_run` maps simulated time ↔ wall-clock time.
- IDs are stable strings (e.g. `SHP-000123`), not database sequences, so runs are reproducible.

## Done: Phase 1 – Replay MVP (v0.1.0)

DataCo replay, core schema v0.1, replay engine, FastAPI (REST + SSE), mini control
tower, `docker compose up`. Results: `docs/phase1-acceptance.md`. Mapping and
assumptions: `docs/datasets.md`. Agreed schema changes against the table above:
`sim_run_id` on movement tables, `engine_version`/`schema_version` on `sim_run`,
shipment status `delayed` (see `schema/README.md`).

## Current phase: Phase 2 – Simulation

Goal: generate supply chains instead of only replaying them, including inventory,
inbound flows and disruptions, and reach consumers by push (webhooks) and agents (MCP).

In scope:
- Scenario YAML format (Pydantic spec → JSON Schema), CC BY 4.0, one folder per scenario with README
- SimPy simulation engine: demand, allocation/backorders, shipments, inventory with
  reorder policies, inbound replenishment, production (bill of materials). Seeded and
  deterministic; it produces the same plan structure as the replay adapters, so
  consumers cannot tell the modes apart
- Templates `wholesale` and `manufacturing`
- Disruption catalog (lane delays, site outages, demand spikes), scheduled in the
  scenario or injected into a running run via the API
- Webhooks (subscriptions, HMAC-signed delivery with retries)
- MCP server on top of the access layer
- Control tower shows inventory and disruptions
- Added at the maintainer's request: a scenario builder (industry, region, size, flows,
  disruption profile → generated scenario, web page) and dataset continuation (replay a
  dataset's end, then a simulation calibrated on it; `dataco-continued`, ADR 0005)

Out of scope for Phase 2: calibration from users' own data, LLM-assisted mapping, Python SDK,
auth, public hosting.

Gate: both templates run for 90 simulated days without crashes, same seed → identical
event stream (tested), an injected disruption changes only events after its start.

## Roadmap (later phases, for context only)

3. Calibration: import own data, LLM-assisted column mapping, parameter estimation, replay → simulation hand-over, Python SDK
4. Community: scenario gallery, frozen benchmark scenarios, Zenodo DOI per release, public demo, teaching notebook

## Licensing rules

| Part | License |
| --- | --- |
| Code | Apache-2.0 |
| Core schema, scenario files | CC BY 4.0 |
| Data generated by simulation runs | CC0 |
| Third-party datasets | original license; only redistributed if allowed |

- **No dependency under Business Source License or other non-OSI licenses in the core** (e.g. SDV may only ever be an optional plugin).
- Check the license of every new dependency and dataset. Note it in the PR.

## Tech stack and conventions

- Python 3.12+, `uv` for environments and dependencies, `ruff` (lint + format), `pytest`, type hints everywhere, `mypy` on `schema/` and `engine/`
- Pydantic v2 models are the single source of truth for the schema
- FastAPI for the API, `psycopg` 3 for Postgres access; plain SQL migrations in `schema/migrations/`
- Postgres 16 in Docker; event distribution via `LISTEN/NOTIFY` in Phase 1
- Config via environment variables (`.env.example` committed, `.env` gitignored)
- Conventional Commits; small, reviewable commits
- Architecture decisions go to `docs/adr/NNNN-title.md`
- Every feature comes with tests; the determinism test (same seed → same events) must always pass

## Working agreements for Claude Code

- Plan before larger changes and show the plan; implement in small steps with a runnable state after each.
- Ask before changing the core schema, licenses or the phase scope.
- Never commit raw datasets, secrets or `.env`.
- Keep the README quickstart working: a stranger must get from `git clone` to a live stream in under 10 minutes.
