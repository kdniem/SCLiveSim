"""Core schema v0.1 as Pydantic models.

These models are the single source of truth. JSON Schema files under
`schema/jsonschema/` are generated from them (`scripts/export_jsonschema.py`)
and the SQL migrations in `schema/migrations/` must match them
(checked by `tests/test_migration.py`).

All timestamps are simulated time in UTC. IDs are stable strings.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from schema.enums import (
    EventType,
    LocationType,
    ObjectType,
    OrderDirection,
    OrderStatus,
    PartnerRole,
    RunMode,
    RunStatus,
    ShipmentStatus,
    TransportMode,
)


def _require_utc(value: datetime) -> datetime:
    offset = value.utcoffset()
    if offset is None or offset.total_seconds() != 0:
        raise ValueError("timestamps must be timezone-aware UTC")
    return value


UtcDatetime = Annotated[datetime, AfterValidator(_require_utc)]
StableId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")]
Attributes = Annotated[dict[str, Any], Field(default_factory=dict)]


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=False)


# --- Master data -----------------------------------------------------------


class Location(CoreModel):
    id: StableId
    type: LocationType
    name: str
    country: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    attributes: Attributes


class Partner(CoreModel):
    id: StableId
    role: PartnerRole
    location_id: StableId | None = None
    attributes: Attributes


class Item(CoreModel):
    id: StableId
    name: str
    category: str | None = None
    unit: str = "pcs"
    weight_kg: Decimal | None = Field(default=None, ge=0)
    value: Decimal | None = Field(default=None, ge=0)
    attributes: Attributes


class Lane(CoreModel):
    id: StableId
    origin_location_id: StableId
    destination_location_id: StableId
    mode: TransportMode
    planned_transit_days: Decimal = Field(ge=0)
    cost: Decimal | None = Field(default=None, ge=0)


# --- Movement data (state per run) -----------------------------------------


class Order(CoreModel):
    sim_run_id: StableId
    id: StableId
    direction: OrderDirection
    partner_id: StableId
    order_date: UtcDatetime
    requested_date: UtcDatetime | None = None
    status: OrderStatus
    attributes: Attributes


class OrderLine(CoreModel):
    sim_run_id: StableId
    id: StableId
    order_id: StableId
    item_id: StableId
    quantity: Decimal = Field(gt=0)
    value: Decimal | None = Field(default=None, ge=0)


class Shipment(CoreModel):
    sim_run_id: StableId
    id: StableId
    order_id: StableId
    lane_id: StableId | None = None
    carrier_id: StableId | None = None
    etd: UtcDatetime | None = None
    eta_planned: UtcDatetime | None = None
    eta_current: UtcDatetime | None = None
    atd: UtcDatetime | None = None
    ata: UtcDatetime | None = None
    status: ShipmentStatus


class Inventory(CoreModel):
    sim_run_id: StableId
    location_id: StableId
    item_id: StableId
    on_hand: Decimal
    reserved: Decimal = Decimal(0)
    in_transit: Decimal = Decimal(0)
    as_of: UtcDatetime


# --- Events ----------------------------------------------------------------


class Event(CoreModel):
    """One immutable entry of the append-only event log."""

    sim_run_id: StableId
    id: StableId
    ts: UtcDatetime
    type: EventType
    object_type: ObjectType
    object_id: StableId
    payload: Attributes


class Affected(CoreModel):
    locations: list[StableId] = Field(default_factory=list)
    lanes: list[StableId] = Field(default_factory=list)


class Disruption(CoreModel):
    sim_run_id: StableId
    id: StableId
    type: str
    affected: Affected
    start: UtcDatetime
    end: UtcDatetime | None = None
    severity: float = Field(ge=0, le=1)


# --- Run -------------------------------------------------------------------


class SimRun(CoreModel):
    id: StableId
    mode: RunMode
    scenario: str
    scenario_version: str
    seed: int
    time_factor: float = Field(gt=0)
    sim_start: UtcDatetime
    wall_start: UtcDatetime | None = None
    status: RunStatus
    engine_version: str
    schema_version: str


ALL_MODELS: dict[str, type[CoreModel]] = {
    "location": Location,
    "partner": Partner,
    "item": Item,
    "lane": Lane,
    "order": Order,
    "order_line": OrderLine,
    "shipment": Shipment,
    "inventory": Inventory,
    "event": Event,
    "disruption": Disruption,
    "sim_run": SimRun,
}
