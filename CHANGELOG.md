# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/); the core schema is versioned separately
(see `schema/README.md`).

## [0.2.0] – Phase 2: Simulation

### Added
- **Simulation mode** (SimPy): demand, FIFO allocation and backorders, shipments
  with transit variability, (s, S) replenishment over inbound lanes, production
  with bills of materials, scheduled disruptions. Deterministic per seed; plays
  through the same engine, clock and controls as replays (ADR 0004).
- **Scenario format** (YAML, CC BY 4.0) with JSON Schema
  (`scenarios/scenario.schema.json`) and the templates `wholesale` and
  `manufacturing`.
- **Disruption catalog**: port closure, carrier strike, extreme weather (lane
  delays); supplier outage, warehouse outage, plant shutdown (site outages);
  demand spike. Scheduled in scenarios or injected into running simulations
  (`POST /runs/{id}/disruptions`); only the future changes.
- **Scenario builder**: generate a scenario from industry (6), region (4), size,
  flows and disruption profile; web page with live preview and network map;
  save, download as YAML, upload own YAML (ADR 0005).
- **Dataset continuation**: `dataco-continued` replays the end of DataCo and
  continues with a simulation calibrated on it; `POST /scenarios/calibrate`
  saves calibrated scenarios (ADR 0005).
- **Webhooks** with HMAC-SHA256 signatures, retries and a queue filled in the
  same transaction as each event; new `webhooks` service.
- **MCP server** (`python -m mcp_server`, stdio or streamable HTTP) with 14 tools.
- Control tower: run picker, inbound/outbound shipments with delay cause,
  inventory with low-stock markers, active disruptions; link to the builder.
- API: `/disruptions`, `/disruption-types`, `/locations`, `/items`, `/lanes`,
  `/scenarios` (create, upload, calibrate, download), `/builder/*`, `/webhooks`.
- Core schema 0.2.0: event type `inventory.changed`.

### Changed
- `docker compose up` autostarts the `wholesale` simulation instead of the DataCo
  replay (no download needed); `LIVESIM_AUTOSTART` takes a comma-separated list.
- `CLAUDE.md` is part of the repository.

### Fixed
- The migration runner kept its advisory lock when the caller never committed,
  which blocked the engine and the API behind the webhook worker.

## [0.1.0] – Phase 1: Replay MVP

### Added
- Core schema v0.1.0: Pydantic models for 11 entities, generated JSON Schema
  (`schema/jsonschema/v0.1/`), SQL migrations, append-only event log with
  `NOTIFY sc_events`.
- DataCo Smart Supply Chain adapter (Mendeley Data V5, CC BY 4.0): checksum-verified
  download, mapping to orders, lines, shipments, customers and items, and a replay
  event plan. Personal data columns are never read.
- Replay engine: dataset "starts now", time factor up to 1,000,000×, seeded
  sampling, pause/resume, exactly-once writes per tick, restart-safe cursor.
- FastAPI access layer: runs (create, list, pause, resume), shipments, orders,
  inventory, events, SSE `/stream` with type filter and `Last-Event-ID` resume,
  OpenAPI spec.
- `docker compose up`: PostgreSQL 16, engine (auto-download + autostart at 1440×),
  API and the mini control tower example.
- Tests: schema, migrations, adapter (synthetic fixture), determinism (same seed →
  identical event stream), pause/resume, API and SSE, one simulated week on the
  real dataset (local only).
- Docs: README quickstart, dataset mapping and assumptions, ADRs 0001–0003,
  CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.

### Known limitations
- DataCo has no inventory, origin, carrier or dispatch time: inventory stays empty,
  departure equals the order date, and no lanes or carriers are created
  (see `docs/datasets.md`).
- The replay plan is held in memory (about 0.5 GB for the full dataset).
- No authentication; run it locally only (see `SECURITY.md`).
