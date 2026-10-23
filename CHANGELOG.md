# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/); the core schema is versioned separately
(see `schema/README.md`).

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
