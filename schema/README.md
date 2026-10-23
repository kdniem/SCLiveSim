# Core schema

The core schema is the most important artifact of SC LiveSim. It is versioned
separately from the code ([SemVer](https://semver.org/), see `schema/__init__.py`)
and licensed under **CC BY 4.0**.

| Path | Content |
| --- | --- |
| `models.py` | Pydantic v2 models: the single source of truth |
| `enums.py` | Allowed values (location types, statuses, event types, …) |
| `jsonschema/vX.Y/` | Generated JSON Schema, one file per entity |
| `migrations/` | Plain SQL migrations for PostgreSQL 16 |

Regenerate the JSON Schema after changing a model:

```bash
uv run python scripts/export_jsonschema.py
```

Apply migrations to the database in `DATABASE_URL`:

```bash
uv run python -m schema.migrate
```

## Rules

- `event` is **append-only**: UPDATE, DELETE and TRUNCATE are rejected by triggers.
  Each insert sends a `NOTIFY sc_events` with `sim_run_id`, `id` and `type`.
- Movement tables (`order`, `order_line`, `shipment`, `inventory`) hold the
  current state **per run** (`sim_run_id` is part of the primary key).
  The engine updates them in the same transaction as the event.
- All timestamps are simulated time in UTC (`timestamptz`).
- IDs are stable strings such as `SHP-000123`.
- Industry-specific fields go into `attributes` (JSONB), not new tables.
- `disruption.severity` ranges from 0 (none) to 1 (complete stop).

## Changes from the v0.1 draft in CLAUDE.md

Agreed with the maintainer before implementation:

- `sim_run_id` added to `order`, `order_line`, `shipment`, `inventory`, `event`
  (key part), so several runs can coexist.
- `engine_version` and `schema_version` added to `sim_run` for reproducibility.
- Shipment status values: `planned`, `picked_up`, `in_transit`, `delayed`,
  `delivered`, `cancelled`.

## Changelog

- **0.2.0**: new event type `inventory.changed` (initial stock, production),
  migration `0003`. Backwards compatible.
- **0.1.0**: first version.
