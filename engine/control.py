"""Run control in the database, shared by the API and the engine.

The API only writes intents (create a run, desired status). The engine process
owns the clock and applies them (see engine.replay.runner).
"""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from engine import ENGINE_VERSION
from engine.clock import Clock
from engine.scenarios import SCENARIOS
from schema import SCHEMA_VERSION
from schema.enums import RunMode, RunStatus

MAX_TIME_FACTOR = 1_000_000.0


@dataclass(frozen=True)
class RunOptions:
    scenario: str = "dataco"
    seed: int = 0
    time_factor: float = 1440.0
    sample_rate: float = 1.0
    dataset_from: datetime | None = None
    dataset_to: datetime | None = None
    sim_start: datetime | None = None
    paused: bool = False

    def validate(self) -> None:
        if self.scenario not in SCENARIOS:
            raise ValueError(f"unknown scenario {self.scenario!r}; known: {sorted(SCENARIOS)}")
        if not 0 < self.time_factor <= MAX_TIME_FACTOR:
            raise ValueError(f"time_factor must be in (0, {MAX_TIME_FACTOR:g}]")
        if not 0 < self.sample_rate <= 1:
            raise ValueError("sample_rate must be in (0, 1]")
        for value in (self.dataset_from, self.dataset_to, self.sim_start):
            if value is not None and value.utcoffset() is None:
                raise ValueError("timestamps must be timezone-aware")

    def source_options(self) -> dict[str, Any]:
        """The subset stored in run_control.options (JSON)."""
        data = asdict(self)
        return {
            "seed": data["seed"],
            "sample_rate": data["sample_rate"],
            "dataset_from": self.dataset_from.isoformat() if self.dataset_from else None,
            "dataset_to": self.dataset_to.isoformat() if self.dataset_to else None,
        }


RUN_COLUMNS = (
    "r.id, r.mode, r.scenario, r.scenario_version, r.seed, r.time_factor, r.sim_start,"
    " r.wall_start, r.status, r.engine_version, r.schema_version,"
    " c.desired_status, c.sim_position, c.wall_anchor, c.cursor AS events_emitted,"
    " c.events_total, c.error, c.options"
)


def _next_run_id(conn: psycopg.Connection) -> str:
    conn.execute("LOCK TABLE sim_run IN SHARE ROW EXCLUSIVE MODE")
    row = conn.execute("SELECT count(*) FROM sim_run").fetchone()
    return f"RUN-{(row[0] if row else 0) + 1:04d}"


def create_run(conn: psycopg.Connection, options: RunOptions, clock: Clock) -> str:
    """Insert a new replay run; the engine starts it on its next tick."""
    options.validate()
    sim_start = (options.sim_start or clock.now()).replace(microsecond=0)
    scenario = SCENARIOS[options.scenario]
    with conn.transaction():
        run_id = _next_run_id(conn)
        conn.execute(
            "INSERT INTO sim_run (id, mode, scenario, scenario_version, seed, time_factor,"
            " sim_start, wall_start, status, engine_version, schema_version)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, NULL, %s, %s, %s)",
            (
                run_id, RunMode.REPLAY.value, scenario.name, scenario.version, options.seed,
                options.time_factor, sim_start, RunStatus.CREATED.value, ENGINE_VERSION,
                SCHEMA_VERSION,
            ),
        )  # fmt: skip
        conn.execute(
            "INSERT INTO run_control (sim_run_id, options, desired_status, sim_position)"
            " VALUES (%s, %s, %s, %s)",
            (
                run_id,
                Jsonb(options.source_options()),
                "paused" if options.paused else "running",
                sim_start,
            ),
        )
    return run_id


def set_desired_status(conn: psycopg.Connection, run_id: str, desired: str) -> bool:
    """Ask the engine to pause or resume a run. Returns False if the run is unknown."""
    if desired not in ("running", "paused"):
        raise ValueError(desired)
    with conn.transaction():
        cur = conn.execute(
            "UPDATE run_control SET desired_status = %s, updated_at = now() WHERE sim_run_id = %s",
            (desired, run_id),
        )
    return cur.rowcount == 1


def get_runs(conn: psycopg.Connection, run_id: str | None = None) -> list[dict[str, Any]]:
    where = "WHERE r.id = %s" if run_id else ""
    params = (run_id,) if run_id else ()
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            f"SELECT {RUN_COLUMNS} FROM sim_run r JOIN run_control c ON c.sim_run_id = r.id"
            f" {where} ORDER BY r.id",
            params,
        )
        return cur.fetchall()
