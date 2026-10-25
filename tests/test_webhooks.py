"""Webhooks: subscription API, queueing trigger, signed delivery and retries."""

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

import api.webhooks as webhooks
from adapters.dataco import DataCoLoader
from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.replay.runner import ReplayEngine

FIXTURE = Path(__file__).parent / "fixtures" / "dataco_sample.csv"


class Receiver:
    def __init__(self, fail: bool = False) -> None:
        self.requests: list[tuple[dict[str, str], bytes]] = []
        self.fail = fail
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                receiver.requests.append((dict(self.headers), body))
                self.send_response(500 if receiver.fail else 204)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


@pytest.fixture
def receiver() -> Iterator[Receiver]:
    r = Receiver()
    yield r
    r.server.shutdown()


@pytest.fixture
def client(db_url: str) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=1))
    with TestClient(app) as test_client:
        yield test_client


def produce_events(client: TestClient, conn: psycopg.Connection) -> str:
    clock = FakeClock(datetime.now(UTC))
    engine = ReplayEngine(clock, loaders={"dataco": DataCoLoader(FIXTURE)})
    run: str = client.post("/runs", json={"time_factor": 100_000}).json()["id"]
    engine.tick(conn)
    for _ in range(10):
        clock.advance(timedelta(seconds=10))
        engine.tick(conn)
    return run


def test_signed_delivery_with_type_filter(
    client: TestClient, conn: psycopg.Connection, receiver: Receiver
) -> None:
    created = client.post(
        "/webhooks", json={"url": receiver.url, "types": ["shipment.delayed"]}
    ).json()
    assert created["id"] == "WHK-0001" and len(created["secret"]) >= 16
    assert "secret" not in client.get("/webhooks").json()[0]

    run = produce_events(client, conn)
    with httpx.Client(timeout=5) as http:
        assert webhooks.deliver_due(conn, http) == 2  # the fixture has two late orders

    assert len(receiver.requests) == 2
    headers, body = receiver.requests[0]
    event: dict[str, Any] = json.loads(body)
    assert event["type"] == "shipment.delayed" and event["sim_run_id"] == run
    assert headers["X-SC-LiveSim-Event"] == "shipment.delayed"
    assert webhooks.verify(
        created["secret"],
        headers["X-SC-LiveSim-Timestamp"],
        body,
        headers["X-SC-LiveSim-Signature"],
    )
    assert not webhooks.verify("wrong-secret-0000", headers["X-SC-LiveSim-Timestamp"], body,
                               headers["X-SC-LiveSim-Signature"])  # fmt: skip
    status = client.get(f"/webhooks/{created['id']}").json()
    assert status["delivered"] == 2 and status["pending"] == 0


def test_retries_then_fails(
    client: TestClient,
    conn: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failing = Receiver(fail=True)
    monkeypatch.setattr(webhooks, "BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(webhooks, "MAX_ATTEMPTS", 3)
    hook = client.post("/webhooks", json={"url": failing.url, "types": ["order.created"]}).json()
    produce_events(client, conn)
    with httpx.Client(timeout=5) as http:
        for _ in range(3):
            webhooks.deliver_due(conn, http)
    status = client.get(f"/webhooks/{hook['id']}").json()
    assert status["failed"] == 6 and status["pending"] == 0  # 6 orders, 3 attempts each
    assert len(failing.requests) == 18
    failing.server.shutdown()


def test_delete_and_validation(client: TestClient, receiver: Receiver) -> None:
    assert client.post("/webhooks", json={"url": "not a url"}).status_code == 422
    assert client.post("/webhooks", json={"url": receiver.url, "types": ["x"]}).status_code == 422
    hook = client.post("/webhooks", json={"url": receiver.url}).json()
    assert client.delete(f"/webhooks/{hook['id']}").status_code == 204
    assert client.get(f"/webhooks/{hook['id']}").json()["active"] is False
    assert client.delete("/webhooks/WHK-9999").status_code == 404
