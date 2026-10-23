"""Phase 1 gate: one simulated week of the real DataCo replay, checked via the API.

Runs only when the dataset is in data/raw/ (it is never committed); skipped in CI.
Uses a fake clock, so the week takes seconds instead of seven minutes at 1440x.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from adapters.dataco import DataCoLoader
from adapters.dataco.fetch import MAIN_FILE, default_raw_dir
from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.replay.runner import ReplayEngine

CSV = Path(__file__).resolve().parents[1] / default_raw_dir() / MAIN_FILE.name
pytestmark = pytest.mark.skipif(not CSV.exists(), reason="DataCo dataset not downloaded")


def test_one_simulated_week(db_url: str, conn: psycopg.Connection) -> None:
    clock = FakeClock(datetime.now(UTC))
    engine = ReplayEngine(clock, loaders={"dataco": DataCoLoader(CSV)})
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=1))
    with TestClient(app) as client:
        run = client.post("/runs", json={"seed": 42, "time_factor": 1440}).json()
        engine.tick(conn)
        for _ in range(7 * 60 * 2):  # 7 wall minutes in 0.5 s ticks = 7 simulated days
            clock.advance(timedelta(seconds=0.5))
            engine.tick(conn)

        state = client.get(f"/runs/{run['id']}").json()
        assert state["status"] == "running", state
        sim_start = datetime.fromisoformat(run["sim_start"])

        rows = conn.execute(
            "SELECT count(*), max(ts), bool_and(ts >= %s) FROM event WHERE sim_run_id = %s",
            (sim_start, run["id"]),
        ).fetchone()
        assert rows is not None
        count, last_ts, after_start = rows
        assert count == state["events_emitted"] > 1000
        assert after_start and last_ts <= sim_start + timedelta(days=7, seconds=1)

        # events are in time order
        out_of_order = conn.execute(
            "SELECT count(*) FROM (SELECT ts < lag(ts) OVER (ORDER BY id) AS bad"
            " FROM event WHERE sim_run_id = %s) t WHERE bad",
            (run["id"],),
        ).fetchone()
        assert out_of_order == (0,)

        open_shipments = client.get("/shipments", params={"open": "true", "limit": 1000}).json()
        delayed = [s for s in open_shipments if s["status"] == "delayed"]
        assert open_shipments and delayed
        assert all(s["ata"] is None for s in open_shipments)
        assert all(s["eta_current"] > s["eta_planned"] for s in delayed)

        delivered = conn.execute(
            "SELECT count(*) FILTER (WHERE ata IS NULL) FROM shipment"
            " WHERE sim_run_id = %s AND status = 'delivered'",
            (run["id"],),
        ).fetchone()
        assert delivered == (0,)
