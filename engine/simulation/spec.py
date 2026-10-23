"""Scenario specification (the format of `scenarios/<name>/scenario.yaml`).

A scenario describes a supply chain network and its behaviour: master data
(reusing the core schema models), stock policies, demand, production and
scheduled disruptions. Scenario files are licensed under CC BY 4.0.

Units: all durations are in days (floats allowed), quantities in item units.
"""

from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from schema.models import Item, Location, Partner, StableId

SPEC_VERSION = "0.1.0"

PositiveDays = Annotated[float, Field(ge=0)]


class SpecModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LaneSpec(SpecModel):
    """A transport lane.

    Actual transit time = planned * lognormal noise with mean `transit_mean_ratio`
    and coefficient of variation `transit_cv`. A ratio below 1 models the buffer
    carriers build into planned transit times.
    """

    id: StableId
    origin_location_id: StableId
    destination_location_id: StableId
    mode: Literal["road", "sea", "air", "rail"]
    planned_transit_days: PositiveDays
    transit_cv: float = Field(0.15, ge=0, le=2, description="Coefficient of variation")
    transit_mean_ratio: float = Field(0.9, gt=0, le=3, description="Mean actual / planned")
    cost: float | None = Field(None, ge=0)
    carrier_id: StableId | None = None


class StockPolicy(SpecModel):
    """Inventory of one item at one location with an (s, S) reorder policy."""

    location_id: StableId
    item_id: StableId
    initial: float = Field(ge=0)
    safety_stock: float = Field(0, ge=0)
    reorder_point: float = Field(ge=0, description="Reorder when on hand + on order <= this")
    order_up_to: float = Field(gt=0)
    review_days: PositiveDays = Field(1.0, description="Policy is checked every n days")
    supplier_id: StableId | None = Field(None, description="Replenish from this supplier")
    inbound_lane_id: StableId | None = None
    supplier_lead_days: PositiveDays = Field(1.0, description="Supplier processing time")

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.order_up_to < self.reorder_point:
            raise ValueError("order_up_to must be >= reorder_point")
        if (self.supplier_id is None) != (self.inbound_lane_id is None):
            raise ValueError("supplier_id and inbound_lane_id go together")
        return self


class DemandStream(SpecModel):
    """Customer orders for one item, served from one location over one lane."""

    customer_id: StableId
    item_id: StableId
    ship_from: StableId
    lane_id: StableId
    orders_per_day: float = Field(gt=0, description="Poisson arrival rate")
    quantity_mean: float = Field(gt=0)
    quantity_cv: float = Field(0.3, ge=0, le=3)
    requested_days: PositiveDays = Field(
        description="Customer wants delivery this many days after ordering"
    )


class Production(SpecModel):
    """Make an item at a location from components (bill of materials)."""

    location_id: StableId
    item_id: StableId
    components: dict[StableId, float] = Field(description="Component item -> qty per unit")
    rate_per_day: float = Field(gt=0, description="Maximum output per day")
    batch_days: PositiveDays = Field(1.0, description="Production runs every n days")


class DisruptionSpec(SpecModel):
    """A scheduled disruption; `type` must be in the disruption catalog."""

    id: StableId
    type: str
    start_day: PositiveDays
    duration_days: float = Field(gt=0)
    severity: float = Field(ge=0, le=1)
    locations: list[StableId] = Field(default_factory=list)
    lanes: list[StableId] = Field(default_factory=list)


class ScenarioSpec(SpecModel):
    spec_version: str = SPEC_VERSION
    name: str = Field(pattern=r"^[a-z0-9_-]+$")
    version: str
    description: str
    license: str = "CC-BY-4.0"
    horizon_days: int = Field(gt=0, le=3650)
    handling_days: PositiveDays = Field(0.25, description="Time from allocation to departure")
    locations: list[Location]
    partners: list[Partner]
    items: list[Item]
    lanes: list[LaneSpec]
    stock: list[StockPolicy] = Field(default_factory=list)
    demand: list[DemandStream] = Field(default_factory=list)
    production: list[Production] = Field(default_factory=list)
    disruptions: list[DisruptionSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _references(self) -> Self:
        from engine.simulation.catalog import CATALOG

        locations = {loc.id for loc in self.locations}
        partners = {p.id for p in self.partners}
        items = {i.id for i in self.items}
        lanes = {lane.id: lane for lane in self.lanes}
        stock = {(s.location_id, s.item_id) for s in self.stock}
        errors: list[str] = []

        def need(kind: str, value: str | None, known: set[str] | dict[str, LaneSpec]) -> None:
            if value is not None and value not in known:
                errors.append(f"unknown {kind} {value!r}")

        for p in self.partners:
            need("location", p.location_id, locations)
        for lane in self.lanes:
            need("location", lane.origin_location_id, locations)
            need("location", lane.destination_location_id, locations)
            need("partner", lane.carrier_id, partners)
        for s in self.stock:
            need("location", s.location_id, locations)
            need("item", s.item_id, items)
            need("partner", s.supplier_id, partners)
            need("lane", s.inbound_lane_id, lanes)
            if s.inbound_lane_id in lanes and lanes[s.inbound_lane_id].destination_location_id != (
                s.location_id
            ):
                errors.append(f"lane {s.inbound_lane_id} does not end at {s.location_id}")
        for d in self.demand:
            need("partner", d.customer_id, partners)
            need("item", d.item_id, items)
            need("lane", d.lane_id, lanes)
            if (d.ship_from, d.item_id) not in stock:
                errors.append(f"no stock policy for {d.item_id} at {d.ship_from}")
            if d.lane_id in lanes and lanes[d.lane_id].origin_location_id != d.ship_from:
                errors.append(f"lane {d.lane_id} does not start at {d.ship_from}")
        for prod in self.production:
            for item in (prod.item_id, *prod.components):
                if (prod.location_id, item) not in stock:
                    errors.append(f"no stock policy for {item} at {prod.location_id}")
        for dis in self.disruptions:
            if dis.type not in CATALOG:
                errors.append(f"unknown disruption type {dis.type!r}")
            for loc in dis.locations:
                need("location", loc, locations)
            for lane_id in dis.lanes:
                need("lane", lane_id, lanes)
        if errors:
            raise ValueError("; ".join(errors))
        return self


def load_scenario(path: Path) -> ScenarioSpec:
    """Load and validate a scenario YAML file."""
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return ScenarioSpec.model_validate(data)
