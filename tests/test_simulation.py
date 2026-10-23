"""Simulation model tests (no database): determinism, invariants, disruptions."""

from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from engine.replay.plan import PlannedEvent, SourceOptions
from engine.simulation.model import ORIGIN, Simulation, simulate
from engine.simulation.spec import ScenarioSpec, load_scenario
from schema.enums import EventType

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"


@pytest.fixture(scope="module", params=["wholesale", "manufacturing"])
def spec(request: pytest.FixtureRequest) -> ScenarioSpec:
    return load_scenario(SCENARIOS / request.param / "scenario.yaml")


def signature(events: list[PlannedEvent] | Any) -> list[tuple[Any, ...]]:
    return [(e.at, e.type, e.object_id, repr(sorted(e.payload.items()))) for e in events]


def test_templates_are_valid(spec: ScenarioSpec) -> None:
    assert spec.horizon_days >= 90
    assert spec.disruptions, "templates should show disruptions"


def test_same_seed_same_plan(spec: ScenarioSpec) -> None:
    a = simulate(spec, SourceOptions(seed=1))
    b = simulate(spec, SourceOptions(seed=1))
    c = simulate(spec, SourceOptions(seed=2))
    assert signature(a.events) == signature(b.events)
    assert signature(a.events) != signature(c.events)


def test_events_are_time_ordered_and_consistent(spec: ScenarioSpec) -> None:
    events = Simulation(spec, 3).run()
    assert [e.at for e in events] == sorted(e.at for e in events)
    assert events[0].at == ORIGIN
    lifecycle: dict[str, list[EventType]] = defaultdict(list)
    for e in events:
        if e.object_type.value == "shipment":
            lifecycle[e.object_id].append(e.type)
    for types in lifecycle.values():
        assert types[0] is EventType.SHIPMENT_DEPARTED
        assert types.count(EventType.SHIPMENT_DELIVERED) <= 1
        assert types.count(EventType.SHIPMENT_DELAYED) <= 1
        if EventType.SHIPMENT_DELIVERED in types:
            assert types[-1] is EventType.SHIPMENT_DELIVERED


def test_inventory_never_negative(spec: ScenarioSpec) -> None:
    for e in Simulation(spec, 4).run():
        for table, row in e.changes:
            if table == "inventory":
                assert row["on_hand"] >= -1e-6, (e.type, row)
                assert row["reserved"] >= -1e-6
                assert row["on_hand"] + 1e-6 >= row["reserved"]


def test_all_flows_happen(spec: ScenarioSpec) -> None:
    events = Simulation(spec, 5).run()
    types = Counter(e.type for e in events)
    for t in (
        EventType.ORDER_CREATED,
        EventType.ORDER_CONFIRMED,
        EventType.SHIPMENT_DEPARTED,
        EventType.SHIPMENT_DELAYED,
        EventType.SHIPMENT_DELIVERED,
        EventType.INVENTORY_CHANGED,
        EventType.DISRUPTION_STARTED,
        EventType.DISRUPTION_ENDED,
    ):
        assert types[t] > 0, t
    directions = Counter(
        e.payload["direction"] for e in events if e.type is EventType.ORDER_CREATED
    )
    assert directions["inbound"] > 0 and directions["outbound"] > 0
    on_time = [
        e.payload["on_time"]
        for e in events
        if e.type is EventType.SHIPMENT_DELIVERED and e.payload["direction"] == "outbound"
    ]
    assert 0.5 < sum(on_time) / len(on_time) < 0.95


def test_injected_disruption_changes_only_the_future(spec: ScenarioSpec) -> None:
    lane = spec.demand[0].lane_id
    injected = {
        "id": "DIS-INJ-001",
        "type": "extreme_weather",
        "start_day": 50.0,
        "duration_days": 5,
        "severity": 1.0,
        "locations": [],
        "lanes": [lane],
    }
    base = simulate(spec, SourceOptions(seed=9)).events
    changed = simulate(spec, SourceOptions(seed=9, disruptions=(injected,))).events
    cut = ORIGIN + timedelta(days=50)
    before_base = [e for e in base if e.at < cut]
    before_changed = [e for e in changed if e.at < cut]
    assert signature(before_base) == signature(before_changed)
    assert signature(base) != signature(changed)
    started = [e for e in changed if e.object_id == "DIS-INJ-001"]
    assert [e.type for e in started] == [EventType.DISRUPTION_STARTED, EventType.DISRUPTION_ENDED]
    causes = {e.payload.get("cause") for e in changed if e.type is EventType.SHIPMENT_DELAYED}
    assert "DIS-INJ-001" in causes


def test_site_outage_holds_departures() -> None:
    spec = load_scenario(SCENARIOS / "manufacturing" / "scenario.yaml")
    events = Simulation(spec, 1).run()
    shutdown = next(d for d in spec.disruptions if d.type == "plant_shutdown")
    start = ORIGIN + timedelta(days=shutdown.start_day)
    end = start + timedelta(days=shutdown.duration_days)
    departures = [
        e
        for e in events
        if e.type is EventType.SHIPMENT_DEPARTED
        and e.payload["origin"]["location_id"] == "LOC-PLANT-STR"
        and start <= e.at < end
    ]
    assert departures == []


def test_invalid_references_are_rejected() -> None:
    raw = load_scenario(SCENARIOS / "wholesale" / "scenario.yaml").model_dump()
    raw["demand"][0]["lane_id"] = "LN-NOPE"
    with pytest.raises(ValidationError, match="unknown lane"):
        ScenarioSpec.model_validate(raw)
    raw = load_scenario(SCENARIOS / "wholesale" / "scenario.yaml").model_dump()
    raw["disruptions"][0]["type"] = "alien_invasion"
    with pytest.raises(ValidationError, match="unknown disruption type"):
        ScenarioSpec.model_validate(raw)
