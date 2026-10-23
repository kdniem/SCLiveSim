"""Replay engine tests: determinism, time factor, pause/resume (fixture data)."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import psycopg
import pytest

from adapters.dataco import DataCoLoader
from engine.clock import FakeClock, sim_now
from engine.control import RunOptions, create_run, get_runs, set_desired_status
from engine.replay.runner import ReplayEngine

FIXTURE = Path(__file__).parent / "fixtures" / "dataco_sample.csv"
WALL_T0 = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
SIM_START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(WALL_T0)


@pytest.fixture
def engine(clock: FakeClock) -> ReplayEngine:
    return ReplayEngine(clock, loaders={"dataco": DataCoLoader(FIXTURE)}, batch_size=3)


def run_to_end(conn: psycopg.Connection, engine: ReplayEngine, clock: FakeClock) -> None:
    for _ in range(1000):
        engine.tick(conn)
        if all(r["status"] in ("finished", "failed") for r in get_runs(conn)):
            return
        clock.advance(timedelta(seconds=1))
    raise AssertionError("runs did not finish")


def event_stream(conn: psycopg.Connection, run_id: str) -> list[tuple[Any, ...]]:
    return conn.execute(
        "SELECT id, ts, type, object_type, object_id, payload FROM event"
        " WHERE sim_run_id = %s ORDER BY id",
        (run_id,),
    ).fetchall()


def digest(stream: list[tuple[Any, ...]]) -> str:
    text = json.dumps(stream, default=str, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def test_sim_now() -> None:
    anchor = WALL_T0
    assert sim_now(SIM_START, None, 60, anchor + timedelta(hours=1)) == SIM_START
    assert sim_now(SIM_START, anchor, 60, anchor + timedelta(minutes=1)) == SIM_START + timedelta(
        hours=1
    )


def test_same_seed_gives_identical_event_stream(
    conn: psycopg.Connection, engine: ReplayEngine, clock: FakeClock
) -> None:
    opts = RunOptions(seed=42, time_factor=100_000, sample_rate=0.7, sim_start=SIM_START)
    run_a = create_run(conn, opts, clock)
    run_b = create_run(conn, opts, clock)
    run_c = create_run(conn, RunOptions(**{**opts.__dict__, "seed": 7}), clock)
    run_to_end(conn, engine, clock)

    a, b, c = (event_stream(conn, r) for r in (run_a, run_b, run_c))
    assert a, "run produced no events"
    assert digest(a) == digest(b)
    assert digest(a) != digest(c)


def test_full_fixture_replay_state(
    conn: psycopg.Connection, engine: ReplayEngine, clock: FakeClock
) -> None:
    run = create_run(conn, RunOptions(time_factor=100_000, sim_start=SIM_START), clock)
    run_to_end(conn, engine, clock)
    assert conn.execute("SELECT count(*) FROM event WHERE sim_run_id = %s", (run,)).fetchone() == (
        18,
    )
    statuses = dict(
        conn.execute(
            "SELECT status, count(*) FROM shipment WHERE sim_run_id = %s GROUP BY status", (run,)
        ).fetchall()
    )
    assert statuses == {"delivered": 5}
    cancelled = conn.execute(
        "SELECT status FROM \"order\" WHERE sim_run_id = %s AND id = 'ORD-000104'", (run,)
    ).fetchone()
    assert cancelled == ("cancelled",)
    # simulated time starts at sim_start
    first = conn.execute(
        "SELECT ts FROM event WHERE sim_run_id = %s ORDER BY id LIMIT 1", (run,)
    ).fetchone()
    assert first == (SIM_START,)


def test_time_factor_and_live_state(
    conn: psycopg.Connection, engine: ReplayEngine, clock: FakeClock
) -> None:
    # 1440x: one wall minute = one simulated day
    run = create_run(conn, RunOptions(time_factor=1440, sim_start=SIM_START), clock)
    engine.tick(conn)  # start
    engine.tick(conn)
    # at sim_start: the first order (00:00) is created and departs
    assert conn.execute("SELECT count(*) FROM event WHERE sim_run_id = %s", (run,)).fetchone() == (
        2,
    )
    clock.advance(timedelta(seconds=90))  # sim 2015-01-02 12:00 equivalent (+1.5 days)
    for _ in range(5):
        engine.tick(conn)
    delayed = conn.execute(
        "SELECT id, eta_planned, eta_current FROM shipment"
        " WHERE sim_run_id = %s AND status = 'delayed'",
        (run,),
    ).fetchall()
    assert [d[0] for d in delayed] == ["SHP-000102"]
    assert delayed[0][2] - delayed[0][1] == timedelta(days=1)


def test_pause_and_resume(conn: psycopg.Connection, engine: ReplayEngine, clock: FakeClock) -> None:
    run = create_run(conn, RunOptions(time_factor=1440, sim_start=SIM_START), clock)
    engine.tick(conn)
    clock.advance(timedelta(seconds=30))  # +12h simulated
    engine.tick(conn)
    assert set_desired_status(conn, run, "paused")
    engine.tick(conn)
    paused = get_runs(conn, run)[0]
    assert paused["status"] == "paused"
    assert paused["sim_position"] == SIM_START + timedelta(hours=12)
    count = paused["events_emitted"]

    clock.advance(timedelta(hours=1))  # would be 60 simulated days
    engine.tick(conn)
    assert get_runs(conn, run)[0]["events_emitted"] == count

    assert set_desired_status(conn, run, "running")
    engine.tick(conn)
    clock.advance(timedelta(seconds=30))
    engine.tick(conn)
    resumed = get_runs(conn, run)[0]
    assert resumed["status"] == "running"
    # paused hour did not count: simulated time is now sim_start + 24h
    ts = conn.execute("SELECT max(ts) FROM event WHERE sim_run_id = %s", (run,)).fetchone()
    assert ts is not None and ts[0] <= SIM_START + timedelta(hours=24)
    assert resumed["events_emitted"] > count


def test_unknown_run_and_invalid_options(conn: psycopg.Connection, clock: FakeClock) -> None:
    assert not set_desired_status(conn, "RUN-9999", "paused")
    with pytest.raises(ValueError):
        create_run(conn, RunOptions(scenario="nope"), clock)
    with pytest.raises(ValueError):
        create_run(conn, RunOptions(time_factor=0), clock)


def test_failed_source_marks_run_failed(conn: psycopg.Connection, clock: FakeClock) -> None:
    def broken(_: object) -> None:
        raise FileNotFoundError("dataset missing")

    engine = ReplayEngine(clock, loaders={"dataco": broken})  # type: ignore[dict-item]
    run = create_run(conn, RunOptions(), clock)
    engine.tick(conn)
    state = get_runs(conn, run)[0]
    assert state["status"] == "failed"
    assert "dataset missing" in state["error"]
