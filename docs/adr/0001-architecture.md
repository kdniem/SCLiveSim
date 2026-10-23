# ADR 0001: Architecture and one-way data flow

- Status: accepted
- Date: 2026-10-23

## Context

Supply chain apps that react to change (alerts, risk scores, ETA tracking, live
dashboards) need a data source that keeps moving. We want one open schema and
one event stream that many consumers can use, whether the data is replayed from
a public dataset, simulated, or calibrated from your own data.

## Decision

1. **One-way flow:** Inputs → Engine → PostgreSQL → Access layer → Apps.
   Apps never write to the database directly. They read data and control runs
   only through the access layer (FastAPI).
2. **One schema, three modes.** Replay, simulation and calibration all write the
   same core schema and the same event stream. Consumers cannot tell the modes apart.
3. **Append-only event log.** The `event` table is never updated or deleted.
   Current state (open shipments, stock) is derived from events and kept in
   state tables that the engine updates in the same transaction as the event.
4. **Simulated time.** All timestamps are simulated time, stored as `timestamptz`
   in UTC. `sim_run` maps simulated time to wall-clock time.
5. **Stable string IDs** (e.g. `SHP-000123`) instead of database sequences, so
   runs are reproducible.
6. **Event distribution** via PostgreSQL `LISTEN/NOTIFY` in Phase 1. No message
   broker until there is a measured need.
7. **Engine and API are separate processes.** The API stores run commands
   (create, pause, resume) in the database; the engine picks them up.
8. **Local first.** Everything runs with `docker compose up`. No hard cloud dependency.
9. **Repository layout** follows `CLAUDE.md` (top-level `schema/`, `engine/`,
   `adapters/`, `api/`). These are application packages, not a library on PyPI;
   the later Python SDK becomes its own package under `sdk/python/`.

## Consequences

- Determinism is testable: the same seed and inputs give the same event rows.
- `LISTEN/NOTIFY` payloads are limited to 8000 bytes, so notifications carry the
  event ID and type; consumers load the full event if needed.
- A message broker or webhooks can be added later behind the access layer
  without changing consumers of the SSE stream.
