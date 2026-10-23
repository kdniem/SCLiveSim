"""MCP server tools against the real API (TestClient) and a simulation run."""

import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import anyio
import psycopg
import pytest
from fastapi.testclient import TestClient

import mcp_server.server as srv
from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.replay.runner import ReplayEngine


@pytest.fixture
def client(db_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=1))
    with TestClient(app) as test_client:
        monkeypatch.setattr(srv, "api", lambda: contextlib.nullcontext(test_client))
        yield test_client


def test_tools_drive_a_simulation(client: TestClient, conn: psycopg.Connection) -> None:
    assert "wholesale" in {s["name"] for s in srv.list_scenarios()}
    with pytest.raises(ValueError, match="no runs yet"):
        srv.run_summary()

    run = srv.start_run(scenario="wholesale", seed=5, time_factor=1440)["id"]
    clock = FakeClock(datetime.now(UTC))
    engine = ReplayEngine(clock, batch_size=5000)
    engine.tick(conn)
    for _ in range(40):  # 20 simulated days
        clock.advance(timedelta(seconds=30))
        engine.tick(conn)

    summary = srv.run_summary()
    assert summary["run"]["id"] == run and summary["open_shipments"] > 0
    assert {i["item_id"] for i in summary["inventory"]} >= {"ITM-HEADSET", "ITM-CHAIR"}

    shipments = srv.get_shipments(limit=5)
    assert 0 < len(shipments) <= 5 and shipments[0]["destination"]["country"]
    events = srv.get_events(types=["order.created"], limit=3)
    assert events["counts"] == {"order.created": 3}
    more = srv.get_events(types=["order.created"], after=events["next_after"], limit=3)
    assert more["events"][0]["id"] > events["next_after"]

    network = srv.get_network()
    assert "LN-SZX-HAM" in {ln["id"] for ln in network["lanes"]}
    accepted = srv.inject_disruption(
        run, "port_closure", severity=0.9, duration_days=7, lanes=["LN-SZX-HAM"]
    )
    assert accepted["status"] == "pending"
    with pytest.raises(ValueError, match="needs at least one lane"):
        srv.inject_disruption(run, "port_closure", severity=0.9, duration_days=7)

    engine.tick(conn)
    clock.advance(timedelta(seconds=30))
    engine.tick(conn)
    active = {d["id"] for d in srv.run_summary(run)["active_disruptions"]}
    assert accepted["id"] in active

    assert srv.pause_run(run)["desired_status"] == "paused"
    assert srv.resume_run(run)["desired_status"] == "running"


def test_tools_are_registered() -> None:
    async def names() -> set[str]:
        tools = await srv.server.list_tools()
        return {t.name for t in tools}

    registered = anyio.run(names)
    assert {"run_summary", "inject_disruption", "get_shipments", "start_run"} <= registered
