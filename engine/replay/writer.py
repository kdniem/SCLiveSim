"""Write master data, events and state changes to PostgreSQL."""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from engine.replay.plan import PlannedEvent, shift
from schema.models import Item, Lane, Location, Partner

# Primary keys of the per-run state tables (sim_run_id is added automatically).
STATE_KEYS: dict[str, tuple[str, ...]] = {
    "order": ("id",),
    "order_line": ("id",),
    "shipment": ("id",),
    "inventory": ("location_id", "item_id"),
    "disruption": ("id",),
}
JSON_COLUMNS = {"attributes", "payload", "affected"}


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def to_json(value: Any) -> Jsonb:
    """Wrap a value as JSONB with deterministic serialization of datetimes/decimals."""
    return Jsonb(value, dumps=lambda v: json.dumps(v, default=_json_default, sort_keys=True))


def event_id(index: int) -> str:
    """Stable event ID from the event's position in the run's plan (0-based)."""
    return f"EVT-{index + 1:08d}"


def insert_master_data(
    conn: psycopg.Connection,
    partners: Sequence[Partner],
    items: Sequence[Item],
    locations: Sequence[Location] = (),
    lanes: Sequence[Lane] = (),
) -> None:
    """Insert master data shared by all runs (existing rows are left unchanged)."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO location (id, type, name, country, lat, lon, attributes)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [
                (loc.id, loc.type.value, loc.name, loc.country, loc.lat, loc.lon,
                 to_json(loc.attributes))
                for loc in locations
            ],
        )  # fmt: skip
        cur.executemany(
            "INSERT INTO partner (id, role, location_id, attributes) VALUES (%s, %s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING",
            [(p.id, p.role.value, p.location_id, to_json(p.attributes)) for p in partners],
        )
        cur.executemany(
            "INSERT INTO item (id, name, category, unit, weight_kg, value, attributes)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
            [
                (i.id, i.name, i.category, i.unit, i.weight_kg, i.value, to_json(i.attributes))
                for i in items
            ],
        )
        cur.executemany(
            "INSERT INTO lane (id, origin_location_id, destination_location_id, mode,"
            " planned_transit_days, cost) VALUES (%s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO NOTHING",
            [
                (ln.id, ln.origin_location_id, ln.destination_location_id, ln.mode.value,
                 ln.planned_transit_days, ln.cost)
                for ln in lanes
            ],
        )  # fmt: skip


def _upsert(cur: psycopg.Cursor, table: str, run_id: str, row: dict[str, Any]) -> None:
    columns = ["sim_run_id", *row]
    values = [run_id, *(to_json(v) if k in JSON_COLUMNS else v for k, v in row.items())]
    keys = ["sim_run_id", *STATE_KEYS[table]]
    updates = [c for c in row if c not in keys]
    query = sql.SQL(
        "INSERT INTO {table} ({cols}) VALUES ({vals}) ON CONFLICT ({keys}) DO UPDATE SET {sets}"
    ).format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(map(sql.Identifier, columns)),
        vals=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
        keys=sql.SQL(", ").join(map(sql.Identifier, keys)),
        sets=sql.SQL(", ").join(
            sql.SQL("{c} = EXCLUDED.{c}").format(c=sql.Identifier(c)) for c in updates
        ),
    )
    cur.execute(query, values)


def write_events(
    conn: psycopg.Connection,
    run_id: str,
    first_index: int,
    events: Sequence[PlannedEvent],
    offset: timedelta,
) -> None:
    """Append events and apply their state changes (call inside a transaction)."""
    with conn.cursor() as cur:
        for n, event in enumerate(events):
            cur.execute(
                "INSERT INTO event (sim_run_id, id, ts, type, object_type, object_id, payload)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    run_id,
                    event_id(first_index + n),
                    event.at + offset,
                    event.type.value,
                    event.object_type.value,
                    event.object_id,
                    to_json(shift(event.payload, offset)),
                ),
            )
            for table, row in event.changes:
                _upsert(cur, table, run_id, shift(row, offset))
