"""Map the DataCo CSV to the core schema and a replay event plan.

What the dataset contains (verified against V5 of the CSV, 180,519 order lines,
65,752 orders, 2015-01-01 to 2018-01-31):

- One row per order line. Shipping mode, delivery status and dates are the same
  for all lines of an order.
- `shipping date (DateOrders)` equals the order date plus `Days for shipping
  (real)` (within +/- 12 hours). It is therefore the *delivery* time; the data
  has no separate dispatch time.
- `Delivery Status` is one of Advance shipping, Shipping on time, Late delivery,
  Shipping canceled. "Late delivery" means real days > scheduled days.
- There is no origin location, carrier, inbound or inventory data.

Mapping (full table and assumptions in docs/datasets.md):

- order date: `order.created`, then `shipment.departed` (shipment in transit)
- order date + scheduled days, late orders only: `shipment.delayed` (new ETA)
- shipping date: `shipment.delivered`

Cancelled orders ("Shipping canceled") create the order with status `cancelled`
and no shipment. Personal fields (names, street, e-mail, password, coordinates,
zip codes) are never read.
"""

import csv
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from adapters.dataco.fetch import ATTRIBUTION, DATASET_VERSION, LICENSE
from engine.replay.plan import PlannedEvent, ReplaySource, SourceOptions, StateChange
from schema.enums import (
    EventType,
    ObjectType,
    OrderDirection,
    OrderStatus,
    PartnerRole,
    ShipmentStatus,
)
from schema.models import Item, Partner

SCENARIO = "dataco"
DATE_FORMAT = "%m/%d/%Y %H:%M"
ENCODING = "latin-1"

# Columns read from the CSV. Everything else is ignored.
COL_ORDER_ID = "Order Id"
COL_ORDER_ITEM_ID = "Order Item Id"
COL_ORDER_DATE = "order date (DateOrders)"
COL_SHIPPING_DATE = "shipping date (DateOrders)"
COL_DAYS_REAL = "Days for shipping (real)"
COL_DAYS_SCHEDULED = "Days for shipment (scheduled)"
COL_DELIVERY_STATUS = "Delivery Status"
COL_SHIPPING_MODE = "Shipping Mode"
COL_ORDER_STATUS = "Order Status"
COL_CUSTOMER_ID = "Order Customer Id"
COL_SEGMENT = "Customer Segment"
COL_MARKET = "Market"
COL_REGION = "Order Region"
COL_COUNTRY = "Order Country"
COL_STATE = "Order State"
COL_CITY = "Order City"
COL_PRODUCT_ID = "Product Card Id"
COL_PRODUCT_NAME = "Product Name"
COL_CATEGORY = "Category Name"
COL_DEPARTMENT = "Department Name"
COL_PRODUCT_PRICE = "Product Price"
COL_QUANTITY = "Order Item Quantity"
COL_LINE_TOTAL = "Order Item Total"

REQUIRED_COLUMNS = (
    COL_ORDER_ID, COL_ORDER_ITEM_ID, COL_ORDER_DATE, COL_SHIPPING_DATE, COL_DAYS_REAL,
    COL_DAYS_SCHEDULED, COL_DELIVERY_STATUS, COL_SHIPPING_MODE, COL_ORDER_STATUS,
    COL_CUSTOMER_ID, COL_SEGMENT, COL_MARKET, COL_REGION, COL_COUNTRY, COL_STATE,
    COL_CITY, COL_PRODUCT_ID, COL_PRODUCT_NAME, COL_CATEGORY, COL_DEPARTMENT,
    COL_PRODUCT_PRICE, COL_QUANTITY, COL_LINE_TOTAL,
)  # fmt: skip

LATE = "Late delivery"
CANCELLED = "Shipping canceled"


def order_id(raw: str) -> str:
    return f"ORD-{int(raw):06d}"


def shipment_id(raw: str) -> str:
    return f"SHP-{int(raw):06d}"


def line_id(raw: str) -> str:
    return f"OL-{int(raw):06d}"


def customer_id(raw: str) -> str:
    return f"CUS-{int(raw):05d}"


def product_id(raw: str) -> str:
    return f"ITM-{int(raw):04d}"


def parse_ts(raw: str) -> datetime:
    """DataCo timestamps have no time zone; we treat them as UTC."""
    return datetime.strptime(raw.strip(), DATE_FORMAT).replace(tzinfo=UTC)


def money(raw: str) -> Decimal:
    return Decimal(raw.strip()).quantize(Decimal("0.01"))


@dataclass
class _Line:
    id: str
    item_id: str
    quantity: Decimal
    value: Decimal


@dataclass
class _Order:
    raw_id: str
    order_date: datetime
    delivered_at: datetime
    days_real: int
    days_scheduled: int
    delivery_status: str
    source_status: str
    shipping_mode: str
    customer: str
    destination: dict[str, str]
    lines: list[_Line] = field(default_factory=list)


def read_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open(encoding=ENCODING, newline="") as f:
        reader = csv.reader(f)
        header = [h.strip() for h in next(reader)]
        missing = [c for c in REQUIRED_COLUMNS if c not in header]
        if missing:
            raise ValueError(f"{path.name} is missing columns: {missing}")
        index = {c: header.index(c) for c in REQUIRED_COLUMNS}
        for row in reader:
            yield {c: row[i] for c, i in index.items()}


@dataclass
class _Parsed:
    orders: dict[str, _Order]
    partners: dict[str, Partner]
    items: dict[str, Item]


def parse(rows: Iterable[dict[str, str]]) -> _Parsed:
    orders: dict[str, _Order] = {}
    partners: dict[str, Partner] = {}
    items: dict[str, Item] = {}
    for row in rows:
        raw_id = row[COL_ORDER_ID].strip()
        order = orders.get(raw_id)
        if order is None:
            order = _Order(
                raw_id=raw_id,
                order_date=parse_ts(row[COL_ORDER_DATE]),
                delivered_at=parse_ts(row[COL_SHIPPING_DATE]),
                days_real=int(row[COL_DAYS_REAL]),
                days_scheduled=int(row[COL_DAYS_SCHEDULED]),
                delivery_status=row[COL_DELIVERY_STATUS].strip(),
                source_status=row[COL_ORDER_STATUS].strip(),
                shipping_mode=row[COL_SHIPPING_MODE].strip(),
                customer=customer_id(row[COL_CUSTOMER_ID]),
                destination={
                    "market": row[COL_MARKET].strip(),
                    "region": row[COL_REGION].strip(),
                    "country": row[COL_COUNTRY].strip(),
                    "state": row[COL_STATE].strip(),
                    "city": row[COL_CITY].strip(),
                },
            )
            orders[raw_id] = order
        item = product_id(row[COL_PRODUCT_ID])
        if item not in items:
            items[item] = Item(
                id=item,
                name=row[COL_PRODUCT_NAME].strip(),
                category=row[COL_CATEGORY].strip(),
                unit="pcs",
                value=money(row[COL_PRODUCT_PRICE]),
                attributes={"department": row[COL_DEPARTMENT].strip()},
            )
        if order.customer not in partners:
            partners[order.customer] = Partner(
                id=order.customer,
                role=PartnerRole.CUSTOMER,
                attributes={"segment": row[COL_SEGMENT].strip()},
            )
        order.lines.append(
            _Line(
                id=line_id(row[COL_ORDER_ITEM_ID]),
                item_id=item,
                quantity=Decimal(int(row[COL_QUANTITY])),
                value=money(row[COL_LINE_TOTAL]),
            )
        )
    return _Parsed(orders, partners, items)


def _f(value: Decimal) -> float:
    return float(value)


def plan_order(order: _Order) -> list[PlannedEvent]:
    """Return the planned events of one order, in dataset time."""
    oid, sid = order_id(order.raw_id), shipment_id(order.raw_id)
    t0 = order.order_date
    total = sum((line.value for line in order.lines), Decimal(0))
    cancelled = order.delivery_status == CANCELLED
    attributes: dict[str, Any] = {
        "shipping_mode": order.shipping_mode,
        "destination": order.destination,
        "source_order_status": order.source_status,
        "source_delivery_status": order.delivery_status,
    }
    eta_planned = t0 + timedelta(days=order.days_scheduled)
    order_row: dict[str, Any] = {
        "id": oid,
        "direction": OrderDirection.OUTBOUND.value,
        "partner_id": order.customer,
        "order_date": t0,
        "requested_date": eta_planned,
        "status": (OrderStatus.CANCELLED if cancelled else OrderStatus.CREATED).value,
        "attributes": attributes,
    }
    lines: list[StateChange] = [
        (
            "order_line",
            {
                "id": line.id,
                "order_id": oid,
                "item_id": line.item_id,
                "quantity": line.quantity,
                "value": line.value,
            },
        )
        for line in order.lines
    ]
    common = {
        "order_id": oid,
        "customer_id": order.customer,
        "shipping_mode": order.shipping_mode,
        "destination": order.destination,
    }
    events = [
        PlannedEvent(
            at=t0,
            rank=0,
            type=EventType.ORDER_CREATED,
            object_type=ObjectType.ORDER,
            object_id=oid,
            payload={
                **common,
                "status": order_row["status"],
                "requested_date": eta_planned,
                "value": _f(total),
                "lines": [
                    {"item_id": ln.item_id, "quantity": _f(ln.quantity), "value": _f(ln.value)}
                    for ln in order.lines
                ],
            },
            changes=(("order", order_row), *lines),
        )
    ]
    if cancelled:
        return events

    shipment: dict[str, Any] = {
        "id": sid,
        "order_id": oid,
        "lane_id": None,
        "carrier_id": None,
        "etd": t0,
        "eta_planned": eta_planned,
        "eta_current": eta_planned,
        "atd": t0,
        "ata": None,
        "status": ShipmentStatus.IN_TRANSIT.value,
    }
    shipped_order = {**order_row, "status": OrderStatus.SHIPPED.value}
    events.append(
        PlannedEvent(
            at=t0,
            rank=1,
            type=EventType.SHIPMENT_DEPARTED,
            object_type=ObjectType.SHIPMENT,
            object_id=sid,
            payload={**common, "status": shipment["status"], "atd": t0, "eta_planned": eta_planned},
            changes=(("shipment", dict(shipment)), ("order", shipped_order)),
        )
    )
    delivered_at = order.delivered_at
    if order.delivery_status == LATE and delivered_at > eta_planned:
        shipment = {**shipment, "status": ShipmentStatus.DELAYED.value, "eta_current": delivered_at}
        events.append(
            PlannedEvent(
                at=eta_planned,
                rank=2,
                type=EventType.SHIPMENT_DELAYED,
                object_type=ObjectType.SHIPMENT,
                object_id=sid,
                payload={
                    **common,
                    "status": shipment["status"],
                    "eta_planned": eta_planned,
                    "eta_current": delivered_at,
                    "delay_days": order.days_real - order.days_scheduled,
                },
                changes=(("shipment", dict(shipment)),),
            )
        )
    shipment = {**shipment, "status": ShipmentStatus.DELIVERED.value, "ata": delivered_at}
    events.append(
        PlannedEvent(
            at=delivered_at,
            rank=3,
            type=EventType.SHIPMENT_DELIVERED,
            object_type=ObjectType.SHIPMENT,
            object_id=sid,
            payload={
                **common,
                "status": shipment["status"],
                "eta_planned": eta_planned,
                "ata": delivered_at,
                "on_time": order.delivery_status != LATE,
            },
            changes=(
                ("shipment", dict(shipment)),
                ("order", {**order_row, "status": OrderStatus.DELIVERED.value}),
            ),
        )
    )
    return events


def build_source(parsed: _Parsed, options: SourceOptions) -> ReplaySource:
    """Select orders (window + seeded sample) and build the replay plan."""
    if not 0 < options.sample_rate <= 1:
        raise ValueError("sample_rate must be in (0, 1]")
    ordered = sorted(parsed.orders.values(), key=lambda o: (o.order_date, int(o.raw_id)))
    rng = random.Random(options.seed)
    selected: list[_Order] = []
    for order in ordered:
        draw = rng.random()  # always draw, so the sample is stable across windows
        if options.dataset_from and order.order_date < options.dataset_from:
            continue
        if options.dataset_to and order.order_date >= options.dataset_to:
            continue
        if draw < options.sample_rate:
            selected.append(order)

    events = sorted(
        (event for order in selected for event in plan_order(order)),
        key=PlannedEvent.sort_key,
    )
    if options.dataset_from:
        origin = options.dataset_from
    elif ordered:
        origin = ordered[0].order_date.replace(hour=0, minute=0)
    else:
        origin = datetime(2015, 1, 1, tzinfo=UTC)
    customers = sorted({o.customer for o in selected})
    item_ids = sorted({line.item_id for o in selected for line in o.lines})
    return ReplaySource(
        scenario=SCENARIO,
        scenario_version=f"mendeley-v{DATASET_VERSION}",
        origin=origin,
        partners=[parsed.partners[c] for c in customers],
        items=[parsed.items[i] for i in item_ids],
        events=events,
        notes={
            "attribution": ATTRIBUTION,
            "license": LICENSE,
            "orders": len(selected),
            "inventory": "not available in this dataset",
        },
    )


class DataCoLoader:
    """Loads the CSV once and builds sources for any run options."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._parsed: _Parsed | None = None

    def parsed(self) -> _Parsed:
        if self._parsed is None:
            self._parsed = parse(read_rows(self.path))
        return self._parsed

    def __call__(self, options: SourceOptions) -> ReplaySource:
        return build_source(self.parsed(), options)
