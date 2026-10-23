"""The replay engine: applies run control and emits due events.

One `tick()` looks at every active run:

- `created`  → load the source, insert master data, start (or stay paused)
- `running`  → pause if requested, else write all events with simulated time <= now
- `paused`   → resume if requested

Each run is handled in its own transaction, so an event and the state changes it
causes are always committed together, and a run's cursor only advances with them.
"""

import logging
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row

from engine.clock import Clock, sim_now
from engine.replay.plan import ReplaySource, SourceLoader, SourceOptions
from engine.replay.writer import insert_master_data, write_events
from engine.scenarios import SCENARIOS
from schema.enums import RunStatus

log = logging.getLogger("sc_livesim.engine")

ACTIVE = (RunStatus.CREATED.value, RunStatus.RUNNING.value, RunStatus.PAUSED.value)


def _options(raw: dict[str, Any], seed: int) -> SourceOptions:
    def ts(key: str) -> datetime | None:
        value = raw.get(key)
        return datetime.fromisoformat(value) if value else None

    return SourceOptions(
        seed=seed,
        sample_rate=float(raw.get("sample_rate", 1.0)),
        dataset_from=ts("dataset_from"),
        dataset_to=ts("dataset_to"),
    )


class ReplayEngine:
    def __init__(
        self,
        clock: Clock,
        loaders: dict[str, SourceLoader] | None = None,
        batch_size: int = 2000,
    ) -> None:
        self.clock = clock
        self.batch_size = batch_size
        self._loaders: dict[str, SourceLoader] = dict(loaders or {})
        self._sources: dict[str, ReplaySource] = {}

    def _loader(self, scenario: str) -> SourceLoader:
        if scenario not in self._loaders:
            self._loaders[scenario] = SCENARIOS[scenario].make_loader()
        return self._loaders[scenario]

    def _source(self, run: dict[str, Any]) -> ReplaySource:
        source = self._sources.get(run["id"])
        if source is None:
            loader = self._loader(run["scenario"])
            source = loader(_options(run["options"], run["seed"]))
            self._sources[run["id"]] = source
        return source

    def tick(self, conn: psycopg.Connection) -> int:
        """Advance all active runs once. Returns the number of events written."""
        rows = conn.execute(
            "SELECT id FROM sim_run WHERE status = ANY(%s) ORDER BY id", (list(ACTIVE),)
        ).fetchall()
        conn.commit()
        written = 0
        for (run_id,) in rows:
            try:
                written += self._step(conn, run_id)
            except Exception as exc:
                conn.rollback()
                log.exception("run %s failed", run_id)
                self._fail(conn, run_id, exc)
        return written

    def _fail(self, conn: psycopg.Connection, run_id: str, exc: Exception) -> None:
        self._sources.pop(run_id, None)
        with conn.transaction():
            conn.execute("UPDATE sim_run SET status = 'failed' WHERE id = %s", (run_id,))
            conn.execute(
                "UPDATE run_control SET error = %s, updated_at = now() WHERE sim_run_id = %s",
                (f"{type(exc).__name__}: {exc}", run_id),
            )

    def _step(self, conn: psycopg.Connection, run_id: str) -> int:
        with conn.transaction(), conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT r.id, r.scenario, r.seed, r.time_factor, r.sim_start, r.status,"
                " c.options, c.desired_status, c.sim_position, c.wall_anchor, c.cursor"
                " FROM sim_run r JOIN run_control c ON c.sim_run_id = r.id"
                " WHERE r.id = %s FOR UPDATE OF r, c SKIP LOCKED",
                (run_id,),
            )
            run = cur.fetchone()
            if run is None or run["status"] not in ACTIVE:
                return 0
            now = self.clock.now()
            status, desired = run["status"], run["desired_status"]

            if status == RunStatus.CREATED.value:
                source = self._source(run)
                insert_master_data(conn, source.partners, source.items)
                running = desired == "running"
                conn.execute(
                    "UPDATE sim_run SET status = %s, wall_start = %s WHERE id = %s",
                    ((RunStatus.RUNNING if running else RunStatus.PAUSED).value, now, run_id),
                )
                conn.execute(
                    "UPDATE run_control SET wall_anchor = %s, events_total = %s,"
                    " updated_at = now() WHERE sim_run_id = %s",
                    (now if running else None, len(source.events), run_id),
                )
                log.info("run %s started with %d planned events", run_id, len(source.events))
                return 0

            if status == RunStatus.PAUSED.value:
                if desired == "running":
                    conn.execute("UPDATE sim_run SET status = 'running' WHERE id = %s", (run_id,))
                    conn.execute(
                        "UPDATE run_control SET wall_anchor = %s, updated_at = now()"
                        " WHERE sim_run_id = %s",
                        (now, run_id),
                    )
                    log.info("run %s resumed", run_id)
                return 0

            current = sim_now(run["sim_position"], run["wall_anchor"], run["time_factor"], now)
            if desired == "paused":
                conn.execute("UPDATE sim_run SET status = 'paused' WHERE id = %s", (run_id,))
                conn.execute(
                    "UPDATE run_control SET sim_position = %s, wall_anchor = NULL,"
                    " updated_at = now() WHERE sim_run_id = %s",
                    (current, run_id),
                )
                log.info("run %s paused at %s", run_id, current.isoformat())
                return 0

            source = self._source(run)
            offset = run["sim_start"] - source.origin
            start: int = run["cursor"]
            end = start
            limit = min(len(source.events), start + self.batch_size)
            while end < limit and source.events[end].at + offset <= current:
                end += 1
            if end > start:
                write_events(conn, run_id, start, source.events[start:end], offset)
                conn.execute(
                    "UPDATE run_control SET cursor = %s, updated_at = now() WHERE sim_run_id = %s",
                    (end, run_id),
                )
            if end >= len(source.events):
                conn.execute("UPDATE sim_run SET status = 'finished' WHERE id = %s", (run_id,))
                self._sources.pop(run_id, None)
                log.info("run %s finished", run_id)
            return end - start
