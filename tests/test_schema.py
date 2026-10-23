import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from schema.enums import EventType, ObjectType, ShipmentStatus
from schema.models import ALL_MODELS, Event, Shipment

ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_event_roundtrip() -> None:
    event = Event(
        sim_run_id="RUN-1",
        id="EVT-00000001",
        ts=T0,
        type=EventType.SHIPMENT_DELAYED,
        object_type=ObjectType.SHIPMENT,
        object_id="SHP-000001",
        payload={"delay_days": 2},
    )
    assert Event.model_validate_json(event.model_dump_json()) == event


def test_naive_and_non_utc_timestamps_are_rejected() -> None:
    base = {"sim_run_id": "RUN-1", "id": "SHP-1", "order_id": "ORD-1", "status": "planned"}
    with pytest.raises(ValidationError):
        Shipment(**base, etd=datetime(2026, 1, 1))
    with pytest.raises(ValidationError):
        Shipment(**base, etd=datetime(2026, 1, 1, tzinfo=timezone(timedelta(hours=2))))
    assert Shipment(**base, etd=T0).status is ShipmentStatus.PLANNED


def test_ids_must_be_stable_strings() -> None:
    with pytest.raises(ValidationError):
        Shipment(sim_run_id="RUN 1", id="SHP-1", order_id="ORD-1", status="planned")


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Shipment.model_validate(
            {"sim_run_id": "R", "id": "S", "order_id": "O", "status": "planned", "x": 1}
        )


def test_all_entities_present() -> None:
    assert set(ALL_MODELS) == {
        "location", "partner", "item", "lane", "order", "order_line",
        "shipment", "inventory", "event", "disruption", "sim_run",
    }  # fmt: skip


def test_committed_json_schema_is_up_to_date() -> None:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "export_jsonschema.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
