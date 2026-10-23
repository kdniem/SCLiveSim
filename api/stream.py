"""SSE stream backed by PostgreSQL LISTEN/NOTIFY.

Every inserted event triggers `NOTIFY sc_events` with its run, ID and type
(see the core migration). Each stream client holds one listening connection,
filters notifications and loads matching events in full.
"""

import json
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import psycopg
from fastapi import Request
from psycopg.rows import dict_row

from api.settings import Settings

CHANNEL = "sc_events"
BACKLOG_LIMIT = 10_000

_SELECT = "SELECT sim_run_id, id, ts, type, object_type, object_id, payload FROM event"


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    raise TypeError(type(value).__name__)


def format_event(row: dict[str, Any]) -> str:
    data = json.dumps(row, default=_default, separators=(",", ":"))
    return f"id: {row['sim_run_id']}:{row['id']}\nevent: {row['type']}\ndata: {data}\n\n"


def _parse_last_event_id(value: str | None) -> tuple[str, str] | None:
    if not value or ":" not in value:
        return None
    run_id, event_id = value.split(":", 1)
    return run_id, event_id


async def event_stream(
    request: Request,
    settings: Settings,
    *,
    run: str | None,
    types: set[str] | None,
    last_event_id: str | None,
) -> AsyncIterator[str]:
    listener = await psycopg.AsyncConnection.connect(settings.database_url, autocommit=True)
    reader = await psycopg.AsyncConnection.connect(
        settings.database_url, autocommit=True, row_factory=dict_row
    )
    try:
        await listener.execute(f"LISTEN {CHANNEL}")
        yield "retry: 3000\n\n"

        # Replay what a reconnecting client missed (only within one run).
        resume = _parse_last_event_id(last_event_id)
        if resume and run and resume[0] == run:
            cur = await reader.execute(
                f"{_SELECT} WHERE sim_run_id = %s AND id > %s ORDER BY id LIMIT %s",
                (run, resume[1], BACKLOG_LIMIT),
            )
            for row in await cur.fetchall():
                if types is None or row["type"] in types:
                    yield format_event(row)

        while not await request.is_disconnected():
            received = False
            async for notify in listener.notifies(timeout=settings.keepalive_seconds):
                received = True
                info = json.loads(notify.payload)
                if run and info["sim_run_id"] != run:
                    continue
                if types is not None and info["type"] not in types:
                    continue
                cur = await reader.execute(
                    f"{_SELECT} WHERE sim_run_id = %s AND id = %s",
                    (info["sim_run_id"], info["id"]),
                )
                found = await cur.fetchone()
                if found:
                    yield format_event(found)
            if not received:
                yield ": keep-alive\n\n"
    finally:
        await listener.close()
        await reader.close()
