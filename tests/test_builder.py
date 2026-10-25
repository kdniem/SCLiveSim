"""Scenario builder: generation, API and running a built scenario."""

import itertools
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

from api.main import create_app
from api.settings import Settings
from engine.clock import FakeClock
from engine.replay.runner import ReplayEngine
from engine.simulation.builder import BuilderRequest, build_scenario, distance_km, transit_days
from engine.simulation.library import INDUSTRIES, REGIONS, City
from engine.simulation.model import Simulation


def test_every_industry_and_region_builds_and_simulates() -> None:
    for industry, region in itertools.product(INDUSTRIES, REGIONS):
        req = BuilderRequest.model_validate(
            {"industry": industry, "region": region, "size": "small", "horizon_days": 60}
        )
        spec = build_scenario(req)
        events = Simulation(spec, 1).run()
        delivered = [e for e in events if e.type.value == "shipment.delivered"]
        assert delivered, (industry, region)
        if INDUSTRIES[industry].produces:
            assert spec.production, industry


def test_same_request_same_scenario_and_seed_varies_layout() -> None:
    req = BuilderRequest.model_validate({"industry": "fashion_retail", "region": "asia_pacific"})
    assert build_scenario(req) == build_scenario(req)
    other = build_scenario(req.model_copy(update={"seed": 1}))
    assert other.locations != build_scenario(req).locations


def test_flow_switches() -> None:
    base = {"industry": "automotive_parts", "region": "europe", "size": "small"}
    full = build_scenario(BuilderRequest.model_validate(base))
    assert full.production and any(s.supplier_id for s in full.stock)
    no_production = build_scenario(BuilderRequest.model_validate({**base, "production": False}))
    assert not no_production.production
    assert all(i.id.startswith("ITM-") for i in no_production.items)
    no_inbound = build_scenario(BuilderRequest.model_validate({**base, "inbound": False}))
    assert not any(s.supplier_id for s in no_inbound.stock)
    assert not [loc for loc in no_inbound.locations if loc.type.value == "supplier_site"]
    calm = build_scenario(BuilderRequest.model_validate({**base, "disruptions": "none"}))
    assert calm.disruptions == []
    rough = build_scenario(BuilderRequest.model_validate({**base, "disruptions": "high"}))
    assert len(rough.disruptions) >= 8


def test_transit_rules_of_thumb() -> None:
    shenzhen, hamburg = City("Shenzhen", "CN", 22.54, 114.06), City("Hamburg", "DE", 53.55, 9.99)
    km = distance_km(shenzhen, hamburg)
    assert 8500 < km < 9500
    assert 25 <= transit_days(km, "sea") <= 35
    assert 3 <= transit_days(km, "air") <= 5
    assert transit_days(300, "road") == 0.8


def test_invalid_requests() -> None:
    with pytest.raises(ValueError, match="unknown industry"):
        BuilderRequest.model_validate({"industry": "space_mining"})
    with pytest.raises(ValueError, match="unknown region"):
        BuilderRequest.model_validate({"region": "atlantis"})


@pytest.fixture
def client(db_url: str) -> Iterator[TestClient]:
    app = create_app(Settings(database_url=db_url, cors_origins=("*",), keepalive_seconds=1))
    with TestClient(app) as test_client:
        yield test_client


def test_build_save_download_upload_and_run(client: TestClient, conn: psycopg.Connection) -> None:
    options = client.get("/builder/options").json()
    assert {i["name"] for i in options["industries"]} == set(INDUSTRIES)
    assert options["defaults"]["size"] == "medium"

    request = {"name": "pharma-latam", "industry": "pharma", "region": "latin_america",
               "size": "small", "disruptions": "low", "horizon_days": 120}  # fmt: skip
    preview = client.post("/builder/preview", json=request).json()
    assert preview["summary"]["locations"]["warehouse"] == 1
    assert preview["spec"]["name"] == "pharma-latam"
    assert client.get("/scenarios/pharma-latam").status_code == 404  # preview does not save

    created = client.post("/scenarios", json=request)
    assert created.status_code == 201 and created.json()["origin"] == "builder"
    assert client.post("/scenarios", json=request).status_code == 409
    listed = {s["name"]: s for s in client.get("/scenarios").json()}
    assert listed["pharma-latam"]["mode"] == "simulation"
    assert listed["wholesale"]["origin"] == "built-in"

    text = client.get("/scenarios/pharma-latam", params={"format": "yaml"}).text
    data = yaml.safe_load(text)
    assert data["name"] == "pharma-latam"
    data["name"] = "pharma-latam-edited"
    data["horizon_days"] = 60
    uploaded = client.post(
        "/scenarios/upload",
        content=yaml.safe_dump(data),
        headers={"content-type": "application/yaml"},
    )
    assert uploaded.status_code == 201, uploaded.text
    bad = client.post(
        "/scenarios/upload", content="name: [", headers={"content-type": "application/yaml"}
    )
    assert bad.status_code == 422
    assert client.get("/scenarios/dataco").status_code == 409

    run = client.post("/runs", json={"scenario": "pharma-latam-edited", "time_factor": 1440}).json()
    clock = FakeClock(datetime.now(UTC))
    engine = ReplayEngine(clock)
    engine.tick(conn)
    for _ in range(20):
        clock.advance(timedelta(seconds=30))
        engine.tick(conn)
    state = client.get(f"/runs/{run['id']}").json()
    assert state["status"] == "running" and state["events_emitted"] > 0
    assert state["mode"] == "simulation" and state["scenario_version"] == "1.0.0"
    assert client.get("/inventory", params={"run": run["id"]}).json()
