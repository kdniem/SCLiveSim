"""SimPy model that turns a scenario into a replay plan.

The simulation runs once over the whole horizon (in simulated days) and records
every event with the state changes it causes, at the moment it happens. The
result is a `ReplaySource`, the same structure the dataset adapters produce, so
the replay engine plays simulated and replayed runs the same way.

Determinism: every random process draws from its own `random.Random`, seeded
from the run seed and the process name. The same scenario, seed and injected
disruptions therefore always give the same plan, and a disruption that starts
at day d leaves every event before d unchanged.

Processes:
- demand: Poisson order arrivals per demand stream, lognormal quantities
- allocation: orders reserve stock FIFO; without stock they wait (backorder)
- fulfilment: handling time, departure, transit with noise and disruptions
- replenishment: (s, S) policies order from suppliers over inbound lanes
- production: plants turn components into products (bill of materials)
- disruptions: start/end events; lane delays, site outages, demand spikes
"""

import math
import random
from collections import deque
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import simpy

from engine.replay.plan import PlannedEvent, ReplaySource, SourceOptions, StateChange
from engine.simulation.catalog import CATALOG
from engine.simulation.spec import DisruptionSpec, LaneSpec, ScenarioSpec, StockPolicy
from schema.enums import (
    EventType,
    ObjectType,
    OrderDirection,
    OrderStatus,
    ShipmentStatus,
)
from schema.models import Lane

# Day 0 of every scenario in plan time; the engine shifts it to the run's sim_start.
ORIGIN = datetime(2025, 1, 1, tzinfo=UTC)
EPS = 1e-9

Process = Generator[simpy.Event, Any, None]


@dataclass(frozen=True)
class Window:
    start: float
    end: float
    severity: float
    disruption_id: str

    def active(self, t: float) -> bool:
        return self.start <= t < self.end


@dataclass
class Stock:
    policy: StockPolicy
    on_hand: float
    reserved: float = 0.0
    in_transit: float = 0.0  # inbound, on the way to this location
    on_order: float = 0.0  # ordered from the supplier, not yet received
    below_safety: bool = False
    backorders: deque["OrderState"] = field(default_factory=deque)

    @property
    def available(self) -> float:
        return self.on_hand - self.reserved

    @property
    def backordered(self) -> float:
        return sum(o.quantity for o in self.backorders)


@dataclass
class OrderState:
    id: str
    direction: OrderDirection
    partner_id: str
    item_id: str
    quantity: float
    lane: LaneSpec
    created: float
    requested: float | None
    row: dict[str, Any]


def disruption_from_dict(data: dict[str, Any]) -> DisruptionSpec:
    return DisruptionSpec.model_validate(data)


class Simulation:
    def __init__(
        self,
        spec: ScenarioSpec,
        seed: int,
        injected: tuple[DisruptionSpec, ...] = (),
        id_namespace: str = "",
    ) -> None:
        self.spec = spec
        self.id_namespace = id_namespace
        self.seed = seed
        self.env = simpy.Environment()
        self.events: list[PlannedEvent] = []
        self.items = {i.id: i for i in spec.items}
        self.lanes = {lane.id: lane for lane in spec.lanes}
        self.locations = {loc.id: loc for loc in spec.locations}
        self.stock = {
            (s.location_id, s.item_id): Stock(s, math.inf if s.unlimited else s.initial)
            for s in spec.stock
        }
        self.disruptions = sorted((*spec.disruptions, *injected), key=lambda d: (d.start_day, d.id))
        self.lane_delays: dict[str, list[Window]] = {}
        self.outages: dict[str, list[Window]] = {}
        self.spikes: dict[str, list[Window]] = {}
        for d in self.disruptions:
            kind = CATALOG[d.type].effect
            window = Window(d.start_day, d.start_day + d.duration_days, d.severity, d.id)
            if kind == "lane_delay":
                for lane_id in d.lanes:
                    self.lane_delays.setdefault(lane_id, []).append(window)
            target = self.outages if kind == "site_outage" else self.spikes
            if kind != "lane_delay":
                for loc in d.locations:
                    target.setdefault(loc, []).append(window)
        self._counters: dict[str, int] = {}

    # --- helpers -------------------------------------------------------------

    def rng(self, name: str) -> random.Random:
        return random.Random(f"{self.seed}:{name}")

    def next_id(self, prefix: str, width: int = 6) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}-{self.id_namespace}{n:0{width}d}"

    def ts(self, day: float) -> datetime:
        # Round to whole seconds so timestamps are stable and readable.
        return ORIGIN + timedelta(seconds=round(day * 86400))

    def emit(
        self,
        type_: EventType,
        object_type: ObjectType,
        object_id: str,
        payload: dict[str, Any],
        changes: tuple[StateChange, ...] = (),
    ) -> None:
        self.events.append(
            PlannedEvent(
                at=self.ts(self.env.now),
                rank=len(self.events),
                type=type_,
                object_type=object_type,
                object_id=object_id,
                payload=payload,
                changes=changes,
            )
        )

    def inv(self, key: tuple[str, str]) -> tuple[StateChange, ...]:
        """The inventory row as a state change, or nothing for unlimited stock."""
        return () if self.stock[key].policy.unlimited else (self.inventory_row(key),)

    def inventory_row(self, key: tuple[str, str]) -> StateChange:
        s = self.stock[key]
        return (
            "inventory",
            {
                "location_id": key[0],
                "item_id": key[1],
                "on_hand": _qty(s.on_hand),
                "reserved": _qty(s.reserved),
                "in_transit": _qty(s.in_transit),
                "as_of": self.ts(self.env.now),
            },
        )

    def place(self, location_id: str) -> dict[str, Any]:
        loc = self.locations[location_id]
        return {"location_id": loc.id, "city": loc.name, "country": loc.country}

    def check_safety(self, key: tuple[str, str]) -> None:
        s = self.stock[key]
        below = s.on_hand < s.policy.safety_stock
        if below and not s.below_safety:
            self.emit(
                EventType.INVENTORY_BELOW_SAFETY_STOCK,
                ObjectType.INVENTORY,
                f"{key[0]}:{key[1]}",
                {
                    "location_id": key[0],
                    "item_id": key[1],
                    "on_hand": _qty(s.on_hand),
                    "safety_stock": s.policy.safety_stock,
                },
                (self.inventory_row(key),),
            )
        s.below_safety = below

    def outage(self, location_id: str, t: float) -> Window | None:
        for w in self.outages.get(location_id, ()):
            if w.active(t):
                return w
        return None

    def spike_factor(self, location_id: str, t: float) -> float:
        factor = 1.0
        for w in self.spikes.get(location_id, ()):
            if w.active(t):
                factor *= 1 + 3 * w.severity
        return factor

    # --- processes -----------------------------------------------------------

    def run(self) -> list[PlannedEvent]:
        for key in sorted(self.stock):
            if not self.stock[key].policy.unlimited:
                self.env.process(self._initial(key))
        for d in self.disruptions:
            self.env.process(self._disruption(d))
        for n, stream in enumerate(self.spec.demand):
            self.env.process(self._demand(n, stream))
        for key in sorted(self.stock):
            if self.stock[key].policy.supplier_id:
                self.env.process(self._replenish(key))
        for n, prod in enumerate(self.spec.production):
            self.env.process(self._production(n, prod))
        self.env.run(until=self.spec.horizon_days)
        return self.events

    def _initial(self, key: tuple[str, str]) -> Process:
        self.emit(
            EventType.INVENTORY_CHANGED,
            ObjectType.INVENTORY,
            f"{key[0]}:{key[1]}",
            {
                "location_id": key[0],
                "item_id": key[1],
                "reason": "initial",
                "delta": _qty(self.stock[key].on_hand),
            },
            (self.inventory_row(key),),
        )
        yield self.env.timeout(0)

    def _disruption(self, d: DisruptionSpec) -> Process:
        yield self.env.timeout(max(0.0, d.start_day - self.env.now))
        row = {
            "id": d.id,
            "type": d.type,
            "affected": {"locations": list(d.locations), "lanes": list(d.lanes)},
            "start": self.ts(d.start_day),
            "end": None,
            "severity": d.severity,
        }
        info = {
            "type": d.type,
            "effect": CATALOG[d.type].effect,
            "severity": d.severity,
            "affected": row["affected"],
            "planned_end": self.ts(d.start_day + d.duration_days),
        }
        self.emit(
            EventType.DISRUPTION_STARTED, ObjectType.DISRUPTION, d.id, info, (("disruption", row),)
        )
        yield self.env.timeout(d.duration_days)
        ended = {**row, "end": self.ts(self.env.now)}
        self.emit(
            EventType.DISRUPTION_ENDED,
            ObjectType.DISRUPTION,
            d.id,
            {**info, "end": ended["end"]},
            (("disruption", ended),),
        )

    def _demand(self, n: int, stream: Any) -> Process:
        rng = self.rng(f"demand:{n}")
        while True:
            rate = stream.orders_per_day * self.spike_factor(stream.ship_from, self.env.now)
            yield self.env.timeout(rng.expovariate(rate))
            quantity = _lognormal_qty(rng, stream.quantity_mean, stream.quantity_cv)
            order = self._create_order(
                "ORD",
                OrderDirection.OUTBOUND,
                stream.customer_id,
                stream.item_id,
                quantity,
                self.lanes[stream.lane_id],
                requested=self.env.now + stream.requested_days,
            )
            key = (stream.ship_from, stream.item_id)
            self.stock[key].backorders.append(order)
            self._allocate(key)

    def _create_order(
        self,
        prefix: str,
        direction: OrderDirection,
        partner_id: str,
        item_id: str,
        quantity: float,
        lane: LaneSpec,
        requested: float | None,
    ) -> OrderState:
        order_id = self.next_id(prefix)
        item = self.items[item_id]
        value = round(float(item.value or 0) * quantity, 2)
        row = {
            "id": order_id,
            "direction": direction.value,
            "partner_id": partner_id,
            "order_date": self.ts(self.env.now),
            "requested_date": self.ts(requested) if requested is not None else None,
            "status": OrderStatus.CREATED.value,
            "attributes": {
                "shipping_mode": lane.service or lane.mode,
                "lane_id": lane.id,
                "origin": self.place(lane.origin_location_id),
                "destination": self.place(lane.destination_location_id),
            },
        }
        line = {
            "id": self.next_id("OL"),
            "order_id": order_id,
            "item_id": item_id,
            "quantity": quantity,
            "value": value,
        }
        order = OrderState(
            order_id, direction, partner_id, item_id, quantity, lane, self.env.now, requested, row
        )
        self.emit(
            EventType.ORDER_CREATED,
            ObjectType.ORDER,
            order_id,
            {
                **self._order_payload(order),
                "status": row["status"],
                "value": value,
                "requested_date": row["requested_date"],
            },
            (("order", dict(row)), ("order_line", line)),
        )
        return order

    def _order_payload(self, order: OrderState) -> dict[str, Any]:
        return {
            "order_id": order.id,
            "direction": order.direction.value,
            "partner_id": order.partner_id,
            "item_id": order.item_id,
            "quantity": order.quantity,
            "shipping_mode": order.lane.service or order.lane.mode,
            "lane_id": order.lane.id,
            "origin": self.place(order.lane.origin_location_id),
            "destination": self.place(order.lane.destination_location_id),
        }

    def _set_order_status(self, order: OrderState, status: OrderStatus) -> StateChange:
        order.row = {**order.row, "status": status.value}
        return ("order", dict(order.row))

    def _allocate(self, key: tuple[str, str]) -> None:
        """Reserve stock for waiting orders, first come first served."""
        s = self.stock[key]
        while s.backorders and s.available + EPS >= s.backorders[0].quantity:
            order = s.backorders.popleft()
            s.reserved += order.quantity
            self.emit(
                EventType.ORDER_CONFIRMED,
                ObjectType.ORDER,
                order.id,
                {
                    **self._order_payload(order),
                    "status": OrderStatus.CONFIRMED.value,
                    "waited_days": round(self.env.now - order.created, 4),
                },
                (self._set_order_status(order, OrderStatus.CONFIRMED), *self.inv(key)),
            )
            self.env.process(self._fulfil(order, key))

    def _fulfil(self, order: OrderState, key: tuple[str, str]) -> Process:
        rng = self.rng(f"ship:{order.id}")
        etd = self.env.now + self.spec.handling_days
        yield self.env.timeout(self.spec.handling_days)
        yield from self._wait_for_site(key[0], rng)
        s = self.stock[key]
        s.on_hand -= order.quantity
        s.reserved -= order.quantity
        changes = (self._set_order_status(order, OrderStatus.SHIPPED), *self.inv(key))
        yield from self._transport(order, etd, rng, departure_changes=changes)
        self.check_safety(key)

    def _wait_for_site(self, location_id: str, rng: random.Random) -> Process:
        while (w := self.outage(location_id, self.env.now)) is not None:
            if w.severity < 1 and rng.random() >= w.severity:
                return
            yield self.env.timeout(w.end - self.env.now)

    def _transport(
        self,
        order: OrderState,
        etd: float,
        rng: random.Random,
        departure_changes: tuple[StateChange, ...] = (),
        on_arrival: Any = None,
    ) -> Process:
        lane = order.lane
        shipment_id = self.next_id("SHP")
        atd = self.env.now
        eta_planned = etd + lane.planned_transit_days
        noise = (
            _lognormal(rng, lane.transit_mean_ratio, lane.transit_cv)
            if lane.transit_cv
            else lane.transit_mean_ratio
        )
        arrival = atd + lane.planned_transit_days * noise
        # Disruption delays, each known from the later of departure and its start.
        known: list[tuple[float, float, str]] = []
        for w in self.lane_delays.get(lane.id, ()):
            if w.start < arrival and w.end > atd:
                delay = w.severity * (w.end - w.start)
                arrival += delay
                known.append((max(atd, w.start), delay, w.disruption_id))

        shipment = {
            "id": shipment_id,
            "order_id": order.id,
            "lane_id": lane.id,
            "carrier_id": lane.carrier_id,
            "etd": self.ts(etd),
            "eta_planned": self.ts(eta_planned),
            "eta_current": self.ts(eta_planned),
            "atd": self.ts(atd),
            "ata": None,
            "status": ShipmentStatus.IN_TRANSIT.value,
        }
        base = {
            **self._order_payload(order),
            "shipment_id": shipment_id,
            "carrier_id": lane.carrier_id,
        }
        eta = atd + lane.planned_transit_days  # current expectation
        for when, delay, _ in known:
            if when <= atd:
                eta += delay
        self.emit(
            EventType.SHIPMENT_DEPARTED,
            ObjectType.SHIPMENT,
            shipment_id,
            {
                **base,
                "status": shipment["status"],
                "atd": shipment["atd"],
                "eta_planned": shipment["eta_planned"],
            },
            (("shipment", dict(shipment)), *departure_changes),
        )
        delayed = False

        def update_eta(new_eta: float, cause: str | None) -> None:
            nonlocal delayed, shipment
            first = not delayed and new_eta > eta_planned + EPS
            status = ShipmentStatus.DELAYED if (delayed or first) else ShipmentStatus.IN_TRANSIT
            shipment = {**shipment, "eta_current": self.ts(new_eta), "status": status.value}
            type_ = EventType.SHIPMENT_DELAYED if first else EventType.SHIPMENT_ETA_CHANGED
            delayed = delayed or first
            self.emit(
                type_,
                ObjectType.SHIPMENT,
                shipment_id,
                {
                    **base,
                    "status": shipment["status"],
                    "eta_planned": shipment["eta_planned"],
                    "eta_current": shipment["eta_current"],
                    "cause": cause,
                    "delay_days": round(new_eta - eta_planned, 4),
                },
                (("shipment", dict(shipment)),),
            )

        if eta > eta_planned + EPS:
            update_eta(eta, "departure_late" if atd > etd + EPS else known[0][2])
        for when, delay, cause in known:
            if when <= atd:
                continue
            yield self.env.timeout(when - self.env.now)
            eta += delay
            update_eta(eta, cause)
        if arrival > eta + EPS:
            # Transit takes longer than expected: noticed when the ETA passes.
            yield self.env.timeout(max(0.0, eta - self.env.now))
            update_eta(arrival, "transit_time")
        yield self.env.timeout(max(0.0, arrival - self.env.now))

        shipment = {
            **shipment,
            "ata": self.ts(self.env.now),
            "status": ShipmentStatus.DELIVERED.value,
        }
        arrival_changes: tuple[StateChange, ...] = ()
        if on_arrival is not None:
            arrival_changes = on_arrival()
        on_time = self.env.now <= eta_planned + EPS
        self.emit(
            EventType.SHIPMENT_DELIVERED,
            ObjectType.SHIPMENT,
            shipment_id,
            {
                **base,
                "status": shipment["status"],
                "eta_planned": shipment["eta_planned"],
                "ata": shipment["ata"],
                "on_time": on_time,
            },
            (
                ("shipment", dict(shipment)),
                self._set_order_status(order, OrderStatus.DELIVERED),
                *arrival_changes,
            ),
        )

    def _replenish(self, key: tuple[str, str]) -> Process:
        s = self.stock[key]
        policy = s.policy
        assert policy.supplier_id and policy.inbound_lane_id
        lane = self.lanes[policy.inbound_lane_id]
        rng = self.rng(f"replenish:{key[0]}:{key[1]}")
        while True:
            position = s.on_hand - s.reserved + s.on_order - s.backordered
            if position <= policy.reorder_point + EPS:
                quantity = math.ceil(policy.order_up_to - position)
                s.on_order += quantity
                order = self._create_order(
                    "PO",
                    OrderDirection.INBOUND,
                    policy.supplier_id,
                    key[1],
                    quantity,
                    lane,
                    requested=self.env.now + policy.supplier_lead_days + lane.planned_transit_days,
                )
                self.env.process(self._supply(order, key, rng))
            yield self.env.timeout(policy.review_days)

    def _supply(self, order: OrderState, key: tuple[str, str], rng: random.Random) -> Process:
        """Supplier confirms, prepares and ships a purchase order to `key`."""
        s = self.stock[key]
        self.emit(
            EventType.ORDER_CONFIRMED,
            ObjectType.ORDER,
            order.id,
            {**self._order_payload(order), "status": OrderStatus.CONFIRMED.value},
            (self._set_order_status(order, OrderStatus.CONFIRMED),),
        )
        etd = self.env.now + s.policy.supplier_lead_days
        yield self.env.timeout(s.policy.supplier_lead_days)
        yield from self._wait_for_site(order.lane.origin_location_id, rng)
        s.in_transit += order.quantity
        departure = (self._set_order_status(order, OrderStatus.SHIPPED), self.inventory_row(key))

        def receive() -> tuple[StateChange, ...]:
            s.in_transit -= order.quantity
            s.on_order -= order.quantity
            s.on_hand += order.quantity
            return (self.inventory_row(key),)

        yield from self._transport(order, etd, rng, departure, on_arrival=receive)
        self.check_safety(key)
        self._allocate(key)

    def _production(self, n: int, prod: Any) -> Process:
        rng = self.rng(f"production:{n}")
        out_key = (prod.location_id, prod.item_id)
        while True:
            yield self.env.timeout(prod.batch_days)
            capacity = prod.rate_per_day * prod.batch_days
            w = self.outage(prod.location_id, self.env.now)
            if w is not None:
                capacity *= 1 - w.severity
            out = self.stock[out_key]
            need = out.policy.order_up_to - out.available + out.backordered
            possible = min(
                (
                    self.stock[(prod.location_id, c)].available / per
                    for c, per in prod.components.items()
                ),
                default=capacity,
            )
            quantity = math.floor(min(capacity, need, possible) + EPS)
            if quantity <= 0:
                continue
            rng.random()  # keep one draw per batch for future yield models
            changes: list[StateChange] = []
            for component, per in sorted(prod.components.items()):
                ckey = (prod.location_id, component)
                self.stock[ckey].on_hand -= per * quantity
                changes.append(self.inventory_row(ckey))
            out.on_hand += quantity
            changes.append(self.inventory_row(out_key))
            self.emit(
                EventType.INVENTORY_CHANGED,
                ObjectType.INVENTORY,
                f"{out_key[0]}:{out_key[1]}",
                {
                    "location_id": out_key[0],
                    "item_id": out_key[1],
                    "reason": "production",
                    "delta": quantity,
                    "consumed": {c: per * quantity for c, per in sorted(prod.components.items())},
                },
                tuple(changes),
            )
            for component in sorted(prod.components):
                self.check_safety((prod.location_id, component))
            self._allocate(out_key)


def _lognormal(rng: random.Random, mean: float, cv: float) -> float:
    sigma = math.sqrt(math.log(1 + cv * cv))
    mu = math.log(mean) - sigma * sigma / 2
    return rng.lognormvariate(mu, sigma)


def _lognormal_qty(rng: random.Random, mean: float, cv: float) -> float:
    if cv == 0:
        return float(max(1, round(mean)))
    return float(max(1, round(_lognormal(rng, mean, cv))))


def _qty(value: float) -> float:
    return round(value, 4)


def core_lanes(spec: ScenarioSpec) -> list[Lane]:
    """The scenario's lanes as core schema lanes (without simulation parameters)."""
    return [Lane.model_validate(ln.model_dump(include=set(Lane.model_fields))) for ln in spec.lanes]


def simulate(spec: ScenarioSpec, options: SourceOptions) -> ReplaySource:
    injected = tuple(disruption_from_dict(d) for d in options.disruptions)
    sim = Simulation(spec, options.seed, injected)
    events = sim.run()
    return ReplaySource(
        scenario=spec.name,
        scenario_version=spec.version,
        origin=ORIGIN,
        partners=spec.partners,
        items=spec.items,
        events=events,
        locations=spec.locations,
        lanes=core_lanes(spec),
        notes={"license": "CC0-1.0 (generated data)", "horizon_days": spec.horizon_days},
    )


class SimulationLoader:
    """Source loader for one scenario spec (used by the engine's scenario registry)."""

    def __init__(self, spec: ScenarioSpec) -> None:
        self.spec = spec

    def __call__(self, options: SourceOptions) -> ReplaySource:
        return simulate(self.spec, options)


def iter_scenarios(root: Any) -> Iterator[Any]:
    yield from sorted(root.glob("*/scenario.yaml"))
