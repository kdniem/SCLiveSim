"""Calibration from DataCo and dataset continuation (synthetic fixture)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

import api.scenarios
from adapters.dataco import DataCoLoader
from adapters.dataco.calibrate import calibrate
from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.continuation import DataCoContinuation
from engine.control import RunOptions, create_run, get_runs
from engine.replay.plan import SourceOptions
from engine.replay.runner import ReplayEngine
from schema.enums import EventType

FIXTURE = Path(__file__).parent / "fixtures" / "dataco_sample.csv"
HANDOVER = datetime(2015, 1, 3, tzinfo=UTC)


@pytest.fixture(scope="module")
def dataset() -> DataCoLoader:
    return DataCoLoader(FIXTURE)


def test_calibration_matches_the_data(dataset: DataCoLoader) -> None:
    spec = calibrate(dataset.parsed(), window_days=3650)
    services = {ln.service: ln for ln in spec.lanes}
    # First Class in the fixture: scheduled 1 day, real 2 days, no variation.
    assert services["First Class"].planned_transit_days == 1
    assert services["First Class"].transit_mean_ratio == 2
    assert services["First Class"].transit_cv == 0
    assert all(s.unlimited for s in spec.stock)
    assert {loc.id for loc in spec.locations} == {"LOC-DC-DATACO", "LOC-REG-CENTRA"}
    # 5 non-cancelled orders between the first (Jan 1, 00:00) and last (Jan 2, 08:00) order
    assert sum(d.orders_per_day for d in spec.demand) == pytest.approx(5 / (32 / 24), rel=0.01)


def test_continuation_replays_then_simulates(dataset: DataCoLoader) -> None:
    continuation = DataCoContinuation(dataset, horizon_days=10)
    assert continuation.handover() == HANDOVER
    source = continuation(SourceOptions(seed=1))
    before = [e for e in source.events if e.at < HANDOVER]
    after = [e for e in source.events if e.at >= HANDOVER]
    assert {e.object_id for e in before if e.type is EventType.ORDER_CREATED} >= {"ORD-000101"}
    created_after = [e for e in after if e.type is EventType.ORDER_CREATED]
    assert created_after and all(e.object_id.startswith("ORD-C") for e in created_after)
    assert {e.payload["shipping_mode"] for e in created_after} <= {
        "Standard Class", "Second Class", "First Class", "Same Day",
    }  # fmt: skip
    assert not [c for e in after for c in e.changes if c[0] == "inventory"]
    assert [e.at for e in source.events] == sorted(e.at for e in source.events)
    again = continuation(SourceOptions(seed=1))
    assert [(e.at, e.object_id) for e in again.events] == [
        (e.at, e.object_id) for e in source.events
    ]


def test_continuation_run_and_calibrate_endpoint(
    db_url: str, conn: psycopg.Connection, dataset: DataCoLoader, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = FakeClock(datetime.now(UTC))
    engine = ReplayEngine(
        clock, loaders={"dataco-continued": DataCoContinuation(dataset, horizon_days=10)}
    )
    run = create_run(conn, RunOptions(scenario="dataco-continued", time_factor=100_000), clock)
    engine.tick(conn)
    for _ in range(20):
        clock.advance(timedelta(seconds=10))
        engine.tick(conn)
    state = get_runs(conn, run)[0]
    assert state["mode"] == "calibration" and state["status"] in ("running", "finished")
    ids = [r[0] for r in conn.execute(
        "SELECT object_id FROM event WHERE sim_run_id = %s AND type = 'order.created'", (run,)
    ).fetchall()]  # fmt: skip
    assert any(i.startswith("ORD-C") for i in ids) and "ORD-000101" in ids

    monkeypatch.setattr(api.scenarios, "_dataset_loader", dataset)
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=1))
    with TestClient(app) as client:
        created = client.post("/scenarios/calibrate", json={"window_days": 1000})
        assert created.status_code == 201, created.text
        assert created.json()["origin"] == "calibration"
        text = client.get("/scenarios/dataco-calibrated", params={"format": "yaml"}).text
        assert "service: First Class" in text and "unlimited: true" in text
        assert client.post("/scenarios/calibrate", json={"window_days": 1000}).status_code == 409
        conflict = client.post(f"/runs/{run}/disruptions", json={"type": "port_closure",
                               "lanes": ["X"], "severity": 1, "duration_days": 1})  # fmt: skip
        assert conflict.status_code == 409


def test_continuation_is_listed() -> None:
    from engine.scenarios import SCENARIOS

    assert SCENARIOS["dataco-continued"].mode.value == "calibration"
