"""Simulation runs through engine, database and API (Phase 2 gate)."""

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.control import RunOptions, create_run, get_runs
from engine.replay.runner import ReplayEngine

SIM_START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def client(db_url: str) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=1))
    with TestClient(app) as test_client:
        yield test_client


def run_days(conn: psycopg.Connection, engine: ReplayEngine, clock: FakeClock, days: float) -> None:
    """Advance a 1440x run by `days` simulated days in 30 s wall steps."""
    for _ in range(int(days * 2)):
        clock.advance(timedelta(seconds=30))
        engine.tick(conn)


def stream_digest(conn: psycopg.Connection, run_id: str, before: datetime | None = None) -> str:
    query = "SELECT id, ts, type, object_id, payload FROM event WHERE sim_run_id = %s"
    params: list[Any] = [run_id]
    if before is not None:
        query += " AND ts < %s"
        params.append(before)
    rows = conn.execute(query + " ORDER BY id", params).fetchall()
    return hashlib.sha256(json.dumps(rows, default=str).encode()).hexdigest()


@pytest.mark.parametrize("scenario", ["wholesale", "manufacturing"])
def test_90_days_and_same_seed(conn: psycopg.Connection, scenario: str) -> None:
    clock = FakeClock(datetime(2026, 10, 23, tzinfo=UTC))
    engine = ReplayEngine(clock, batch_size=5000)
    opts = RunOptions(scenario=scenario, seed=11, time_factor=1440, sim_start=SIM_START)
    run_a = create_run(conn, opts, clock)
    run_b = create_run(conn, opts, clock)
    engine.tick(conn)
    run_days(conn, engine, clock, 90)

    for run in get_runs(conn):
        assert run["status"] == "running", run
        assert run["mode"] == "simulation"
    assert stream_digest(conn, run_a) == stream_digest(conn, run_b)

    counts = dict(
        conn.execute(
            "SELECT type, count(*) FROM event WHERE sim_run_id = %s GROUP BY type", (run_a,)
        ).fetchall()
    )
    assert counts["order.created"] > 100 and counts["shipment.delivered"] > 100
    assert counts["disruption.started"] >= 1
    inventory = conn.execute(
        "SELECT count(*), min(on_hand) FROM inventory WHERE sim_run_id = %s", (run_a,)
    ).fetchone()
    assert inventory is not None and inventory[0] >= 4 and inventory[1] >= 0
    last = conn.execute("SELECT max(ts) FROM event WHERE sim_run_id = %s", (run_a,)).fetchone()
    assert last is not None and last[0] <= SIM_START + timedelta(days=90)


def test_injected_disruption_via_api(client: TestClient, conn: psycopg.Connection) -> None:
    clock = FakeClock(datetime.now(UTC))
    engine = ReplayEngine(clock, batch_size=5000)
    body = {"scenario": "wholesale", "seed": 3, "time_factor": 1440,
            "sim_start": SIM_START.isoformat()}  # fmt: skip
    run_a = client.post("/runs", json=body).json()["id"]
    run_b = client.post("/runs", json=body).json()["id"]
    engine.tick(conn)
    run_days(conn, engine, clock, 10)

    bad = client.post(f"/runs/{run_a}/disruptions", json={"type": "port_closure",
                      "lanes": ["LN-NOPE"], "severity": 1, "duration_days": 3})  # fmt: skip
    assert bad.status_code == 422
    accepted = client.post(
        f"/runs/{run_a}/disruptions",
        json={"type": "carrier_strike", "lanes": ["LN-HAM-BER", "LN-HAM-CGN"],
              "severity": 1.0, "duration_days": 4},
    )  # fmt: skip
    assert accepted.status_code == 202, accepted.text
    disruption_id = accepted.json()["id"]
    injected_at = SIM_START + timedelta(days=10)
    run_days(conn, engine, clock, 10)

    assert all(r["status"] == "running" for r in get_runs(conn))
    # Everything before the injection is identical to the untouched run ...
    assert stream_digest(conn, run_a, before=injected_at) == stream_digest(
        conn, run_b, before=injected_at
    )
    # ... and the future differs.
    assert stream_digest(conn, run_a) != stream_digest(conn, run_b)

    disruptions = client.get("/disruptions", params={"run": run_a}).json()
    assert disruption_id in {d["id"] for d in disruptions}
    caused = client.get(
        "/events", params={"run": run_a, "type": "shipment.delayed", "limit": 1000}
    ).json()
    assert any(e["payload"].get("cause") == disruption_id for e in caused)

    replay = client.post("/runs", json={"scenario": "dataco"}).json()["id"]
    conflict = client.post(f"/runs/{replay}/disruptions", json={"type": "port_closure",
                           "lanes": ["X"], "severity": 1, "duration_days": 1})  # fmt: skip
    assert conflict.status_code == 409


def test_catalog_and_master_data(client: TestClient, conn: psycopg.Connection) -> None:
    types = {t["name"]: t for t in client.get("/disruption-types").json()}
    assert types["port_closure"]["targets"] == "lanes"
    scenarios = {s["name"]: s for s in client.get("/scenarios").json()}
    assert scenarios["wholesale"]["mode"] == "simulation"
    assert scenarios["dataco"]["mode"] == "replay"

    clock = FakeClock(datetime.now(UTC))
    client.post("/runs", json={"scenario": "manufacturing"})
    ReplayEngine(clock).tick(conn)
    assert "LOC-PLANT-STR" in {loc["id"] for loc in client.get("/locations").json()}
    assert "FG-PUMP" in {i["id"] for i in client.get("/items").json()}
    assert "LN-HSZ-STR" in {ln["id"] for ln in client.get("/lanes").json()}
