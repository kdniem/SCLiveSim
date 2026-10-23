"""Calibrate a simulation scenario from the DataCo dataset.

The scenario continues the dataset where it ends: same products, the same
destination regions, and transit times, lateness and order volumes estimated
from the last `window_days` of data.

What is estimated, per the data:
- order arrivals per (destination region, shipping class, product): Poisson rate
- order quantity: mean and coefficient of variation per stream
- transit per shipping class: planned = scheduled days; actual/planned ratio and
  variability from the real shipping days

Simplifications (documented in docs/datasets.md):
- One simulated order has one line: the order's highest-value line. Order counts
  per day match the data; item volume is lower than in multi-line orders.
- Customers are aggregated per destination region (`CUS-REG-…`).
- The origin is unknown in DataCo: one placeholder DC without coordinates.
  Lanes use mode `road` as a placeholder and carry the real shipping class as
  `service`, which consumers see as `shipping_mode`.
- DataCo has no inventory: stock is `unlimited` (no inventory rows or events).
- Cancelled orders are not simulated; products outside the top N are scaled in.
"""

import math
import statistics
from collections import defaultdict
from datetime import timedelta

from adapters.dataco.fetch import DATASET_VERSION
from adapters.dataco.mapping import CANCELLED, _Order, _Parsed
from engine.simulation.builder import _slug
from engine.simulation.spec import ScenarioSpec

CALIBRATION_VERSION = "1"
DC_ID = "LOC-DC-DATACO"
CLASS_CODES = {"Standard Class": "STD", "Second Class": "SND", "First Class": "FST",
               "Same Day": "SDY"}  # fmt: skip
SAME_DAY_PLANNED = 0.25  # scheduled "0 days"; a quarter day keeps the ratio meaningful
MIN_RATIO = 0.05  # all deliveries in the window were instant (real days = 0)


def _main_line_item(order: _Order) -> tuple[str, float]:
    line = max(order.lines, key=lambda ln: (ln.value, ln.item_id))
    return line.item_id, float(line.quantity)


def calibrate(
    parsed: _Parsed,
    *,
    name: str = "dataco-calibrated",
    window_days: int = 365,
    top_items: int = 20,
    horizon_days: int = 365,
) -> ScenarioSpec:
    end = max(o.order_date for o in parsed.orders.values())
    start = end - timedelta(days=window_days)
    window = [
        o for o in parsed.orders.values()
        if o.order_date > start and o.delivery_status != CANCELLED
    ]  # fmt: skip
    if not window:
        raise ValueError("no orders in the calibration window")
    days = max(1.0, (end - min(o.order_date for o in window)).total_seconds() / 86400)

    counts: dict[str, int] = defaultdict(int)
    for o in window:
        counts[_main_line_item(o)[0]] += 1
    top = sorted(counts, key=lambda i: (-counts[i], i))[:top_items]
    covered = sum(counts[i] for i in top)
    scale = len(window) / covered

    # Transit per shipping class.
    real_days: dict[str, list[float]] = defaultdict(list)
    scheduled: dict[str, int] = {}
    for o in window:
        real_days[o.shipping_mode].append(o.days_real)
        scheduled[o.shipping_mode] = o.days_scheduled

    used: set[str] = set()
    region_ids = {r: _slug(r, used) for r in sorted({o.destination["region"] for o in window})}
    groups: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for o in window:
        item, qty = _main_line_item(o)
        if item in top:
            groups[(o.destination["region"], o.shipping_mode, item)].append(qty)

    lanes = []
    for rid in region_ids.values():
        for service, code in CLASS_CODES.items():
            if service not in scheduled:
                continue
            planned = float(scheduled[service]) or SAME_DAY_PLANNED
            real = real_days[service]
            mean = statistics.fmean(real)
            cv = statistics.pstdev(real) / mean if mean > 0 else 0.0
            ratio = round(max(mean / planned, MIN_RATIO), 4)
            lanes.append({
                "id": f"LN-DATACO-{rid}-{code}", "origin_location_id": DC_ID,
                "destination_location_id": f"LOC-REG-{rid}", "mode": "road",
                "planned_transit_days": planned, "transit_mean_ratio": ratio,
                "transit_cv": round(min(cv, 2.0), 4), "service": service,
            })  # fmt: skip

    demand = []
    for (region, service, item), qtys in sorted(groups.items()):
        rid = region_ids[region]
        mean_qty = statistics.fmean(qtys)
        cv = statistics.pstdev(qtys) / mean_qty if len(qtys) > 1 else 0.3
        demand.append({
            "customer_id": f"CUS-REG-{rid}", "item_id": item, "ship_from": DC_ID,
            "lane_id": f"LN-DATACO-{rid}-{CLASS_CODES[service]}",
            "orders_per_day": round(len(qtys) / days * scale, 5),
            "quantity_mean": round(mean_qty, 3), "quantity_cv": round(min(cv, 3.0), 4),
            "requested_days": float(scheduled[service]),
        })  # fmt: skip

    markets = {o.destination["region"]: o.destination["market"] for o in window}
    spec = {
        "name": name,
        "version": f"mendeley-v{DATASET_VERSION}-cal{CALIBRATION_VERSION}",
        "description": (
            f"Calibrated from the last {window_days} days of the DataCo Smart Supply Chain "
            f"dataset (CC BY 4.0): {len(window)} orders, {len(region_ids)} destination regions, "
            f"top {len(top)} products covering {covered / len(window):.0%} of orders."
        ),
        "horizon_days": horizon_days,
        "handling_days": 0.0,  # DataCo shipping days count from the order date
        "locations": [
            {"id": DC_ID, "type": "warehouse", "name": "DataCo DC (origin unknown)",
             "attributes": {"placeholder": True}},
            *({"id": f"LOC-REG-{rid}", "type": "customer_site", "name": region,
               "attributes": {"market": markets[region], "aggregated": True}}
              for region, rid in region_ids.items()),
        ],
        "partners": [
            {"id": f"CUS-REG-{rid}", "role": "customer", "location_id": f"LOC-REG-{rid}",
             "attributes": {"segment": "aggregated region", "region": region}}
            for region, rid in region_ids.items()
        ],
        "items": [parsed.items[i].model_dump() for i in sorted(top)],
        "lanes": lanes,
        "stock": [{"location_id": DC_ID, "item_id": i, "unlimited": True} for i in sorted(top)],
        "demand": demand,
    }  # fmt: skip
    result = ScenarioSpec.model_validate(spec)
    assert math.isfinite(sum(d.orders_per_day for d in result.demand))
    return result
