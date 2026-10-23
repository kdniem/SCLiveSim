"""API tests: REST via TestClient, SSE against a real uvicorn server."""

import json
import socket
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import psycopg
import pytest
import uvicorn
from fastapi.testclient import TestClient

from adapters.dataco import DataCoLoader
from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.replay.runner import ReplayEngine

FIXTURE = Path(__file__).parent / "fixtures" / "dataco_sample.csv"
SIM_START = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def client(db_url: str) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=0.5))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def engine() -> tuple[ReplayEngine, FakeClock]:
    # The API stamps runs with the real clock; start the fake clock there too.
    clock = FakeClock(datetime.now(UTC))
    return ReplayEngine(clock, loaders={"dataco": DataCoLoader(FIXTURE)}), clock


def advance(conn: psycopg.Connection, engine: tuple[ReplayEngine, FakeClock], s: float) -> None:
    eng, clock = engine
    eng.tick(conn)  # start newly created runs before time moves
    clock.advance(timedelta(seconds=s))
    for _ in range(5):
        eng.tick(conn)


def test_openapi_lists_phase1_endpoints(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/runs", "/runs/{run_id}/pause", "/runs/{run_id}/resume", "/shipments",
                 "/orders", "/inventory", "/stream"):  # fmt: skip
        assert path in paths


def test_run_lifecycle_and_data(
    client: TestClient, conn: psycopg.Connection, engine: tuple[ReplayEngine, FakeClock]
) -> None:
    assert client.get("/shipments").status_code == 404  # no runs yet
    created = client.post(
        "/runs", json={"seed": 1, "time_factor": 1440, "sim_start": SIM_START.isoformat()}
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["id"] == "RUN-0001" and run["status"] == "created"
    assert run["scenario_version"] == "mendeley-v5"

    advance(conn, engine, 90)  # 1.5 simulated days
    state = client.get(f"/runs/{run['id']}").json()
    assert state["status"] == "running" and state["events_emitted"] > 0

    shipments = client.get("/shipments", params={"open": "true"})
    assert shipments.headers["X-SC-LiveSim-Run"] == "RUN-0001"
    open_ids = {s["id"] for s in shipments.json()}
    assert "SHP-000102" in open_ids and "SHP-000103" not in open_ids  # 103 delivered same day
    delayed = client.get("/shipments", params={"delayed": "true", "expand": "order"}).json()
    assert [s["id"] for s in delayed] == ["SHP-000102"]
    assert delayed[0]["order"]["attributes"]["shipping_mode"] == "First Class"

    orders = client.get("/orders", params={"status": "cancelled"}).json()
    assert [o["id"] for o in orders] == ["ORD-000104"]

    inventory = client.get("/inventory")
    assert inventory.json() == [] and "no inventory" in inventory.headers["X-SC-LiveSim-Note"]

    events = client.get("/events", params={"type": "shipment.delayed"}).json()
    assert [e["object_id"] for e in events] == ["SHP-000102"]

    assert client.post(f"/runs/{run['id']}/pause").json()["desired_status"] == "paused"
    advance(conn, engine, 1)
    assert client.get(f"/runs/{run['id']}").json()["status"] == "paused"
    assert client.post(f"/runs/{run['id']}/resume").status_code == 202


def test_validation_errors(client: TestClient) -> None:
    assert client.post("/runs", json={"scenario": "nope"}).status_code == 422
    assert client.post("/runs", json={"time_factor": 0}).status_code == 422
    assert client.post("/runs/RUN-9999/pause").status_code == 404
    assert client.get("/stream", params={"types": "bogus"}).status_code == 422
    assert client.get("/shipments", params={"run": "RUN-9999"}).status_code == 404


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
        return port


def test_sse_stream_filters_by_type(
    db_url: str, conn: psycopg.Connection, engine: tuple[ReplayEngine, FakeClock]
) -> None:
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=0.5))
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        run = httpx.post(f"{base}/runs", json={"time_factor": 1440}).json()["id"]

        received: list[dict[str, str]] = []

        def consume() -> None:
            params = {"run": run, "types": "shipment.departed,shipment.delayed"}
            with httpx.stream("GET", f"{base}/stream", params=params, timeout=10) as resp:
                assert resp.headers["content-type"].startswith("text/event-stream")
                current: dict[str, str] = {}
                for line in resp.iter_lines():
                    if line.startswith(("id:", "event:", "data:")):
                        key, _, value = line.partition(":")
                        current[key] = value.strip()
                    elif line == "" and "data" in current:
                        received.append(current)
                        current = {}
                        if len(received) >= 3:
                            return

        reader = threading.Thread(target=consume, daemon=True)
        reader.start()
        time.sleep(0.5)  # let the client subscribe
        advance(conn, engine, 120)  # two simulated days
        reader.join(timeout=10)

        assert len(received) >= 3, received
        assert {r["event"] for r in received} <= {"shipment.departed", "shipment.delayed"}
        first = json.loads(received[0]["data"])
        assert first["sim_run_id"] == run and received[0]["id"] == f"{run}:{first['id']}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
