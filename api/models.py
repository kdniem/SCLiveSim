"""Request and response models of the API (core models are reused where possible)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from engine.control import MAX_TIME_FACTOR
from schema.models import Order, Shipment


class RunCreate(BaseModel):
    scenario: str = Field("dataco", description="Scenario (dataset) to replay, see GET /scenarios")
    seed: int = Field(0, description="Seed for sampling; same seed, same event stream")
    time_factor: float = Field(
        1440, gt=0, le=MAX_TIME_FACTOR, description="Simulated seconds per wall-clock second"
    )
    sample_rate: float = Field(1.0, gt=0, le=1, description="Share of orders to replay")
    dataset_from: datetime | None = Field(None, description="Replay dataset from this time")
    dataset_to: datetime | None = Field(None, description="Replay dataset until this time")
    sim_start: datetime | None = Field(
        None, description="Simulated time that dataset_from maps to (default: now)"
    )
    paused: bool = Field(False, description="Create the run paused")


class RunOut(BaseModel):
    id: str
    mode: str
    scenario: str
    scenario_version: str
    seed: int
    time_factor: float
    sim_start: datetime
    wall_start: datetime | None
    status: str
    desired_status: str
    sim_time: datetime = Field(description="Current simulated time of the run")
    events_emitted: int
    events_total: int | None
    engine_version: str
    schema_version: str
    options: dict[str, Any]
    error: str | None = None


class DisruptionTypeOut(BaseModel):
    name: str
    effect: str
    targets: str = Field(description="Whether the disruption affects lanes or locations")
    description: str


class DisruptionCreate(BaseModel):
    type: str = Field(description="A type from GET /disruption-types")
    severity: float = Field(ge=0, le=1)
    duration_days: float = Field(gt=0, le=365)
    lanes: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    start: datetime | None = Field(
        None, description="Simulated start time (default: now; never earlier than now)"
    )


class DisruptionAccepted(BaseModel):
    id: str
    run_id: str
    status: str = "pending"


class ShipmentOut(Shipment):
    order: Order | None = Field(None, description="The shipment's order (with expand=order)")
