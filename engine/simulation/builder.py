"""Scenario builder: generate a complete scenario from a few choices.

Pick an industry, a region, a size, which flows to simulate and a disruption
profile; the builder lays out sites, customers and suppliers on real cities,
derives lanes and transit times from distances, sizes stock policies from the
expected demand and schedules disruptions. The same request always produces
the same scenario.
"""

import math
import random
import unicodedata
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from engine.simulation.library import (
    DISRUPTION_PROFILES,
    INDUSTRIES,
    REGIONS,
    SIZES,
    City,
    Industry,
    Product,
)
from engine.simulation.spec import ScenarioSpec

BUILDER_VERSION = "1"


class BuilderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        None, pattern=r"^[a-z0-9_-]{3,60}$", description="Scenario name (default: generated)"
    )
    industry: str = Field("consumer_electronics", description="See GET /builder/options")
    region: str = Field("europe", description="See GET /builder/options")
    size: Literal["small", "medium", "large"] = "medium"
    inbound: bool = Field(True, description="Simulate replenishment from suppliers")
    production: bool = Field(True, description="Simulate production (manufacturing industries)")
    disruptions: Literal["none", "low", "medium", "high"] = "medium"
    demand_level: float = Field(1.0, ge=0.2, le=5, description="Demand multiplier")
    horizon_days: int = Field(365, ge=30, le=1825)
    seed: int = Field(0, ge=0, description="Varies the layout; same seed, same scenario")

    @field_validator("industry")
    @classmethod
    def _industry(cls, value: str) -> str:
        if value not in INDUSTRIES:
            raise ValueError(f"unknown industry; choose from {sorted(INDUSTRIES)}")
        return value

    @field_validator("region")
    @classmethod
    def _region(cls, value: str) -> str:
        if value not in REGIONS:
            raise ValueError(f"unknown region; choose from {sorted(REGIONS)}")
        return value

    def default_name(self) -> str:
        return f"{self.industry}-{self.region}-{self.size}-{self.seed}".replace("_", "-")


# --- geometry ----------------------------------------------------------------


def distance_km(a: City, b: City) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a.lat, a.lon, b.lat, b.lon))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return 2 * 6371 * math.asin(math.sqrt(h))


def transit_days(km: float, mode: str) -> float:
    """Planned door-to-door transit time by mode (rough industry rules of thumb)."""
    if mode == "sea":
        return round(km * 1.8 / 650 + 6, 1)  # routing detour, ~15 knots, port time
    if mode == "air":
        return round(2 + km / 5000, 1)  # handling and customs dominate
    if mode == "rail":
        return round(1 + km / 400, 1)
    return round(max(0.5, km / 550 + 0.3), 1)  # road: ~550 km per driving day


def _slug(name: str, used: set[str]) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = "".join(ch for ch in ascii_name.upper() if ch.isalpha())[:6] or "SITE"
    slug, n = base, 2
    while slug in used:
        slug, n = f"{base}{n}", n + 1
    used.add(slug)
    return slug


# --- builder -----------------------------------------------------------------


def build_scenario(request: BuilderRequest) -> ScenarioSpec:
    industry = INDUSTRIES[request.industry]
    region = REGIONS[request.region]
    size = SIZES[request.size]
    rng = random.Random(
        f"builder:{BUILDER_VERSION}:{request.industry}:{request.region}:{request.size}:"
        f"{request.seed}"
    )
    produce = industry.produces and request.production
    used: set[str] = set()

    # Products: producing industries make all their products; others pick a range.
    products = list(industry.products)
    if not industry.produces:
        products = sorted(
            rng.sample(products, min(size["products"], len(products))), key=lambda p: p.code
        )
    product_ids = {p.code: (f"FG-{p.code}" if produce else f"ITM-{p.code}") for p in products}

    sites = rng.sample(region.hubs, min(size["sites"], len(region.hubs)))
    customers = rng.sample(region.customers, min(size["customers"], len(region.customers)))
    site_type = "plant" if produce else "warehouse"
    site_ids = {s.name: f"LOC-{'PLT' if produce else 'DC'}-{_slug(s.name, used)}" for s in sites}
    customer_ids = {c.name: _slug(c.name, used) for c in customers}

    # Suppliers: domestic and overseas, by the industry's sourcing share.
    supplied: list[Product]
    if produce:
        codes = sorted({c for p in products for c in p.components})
        supplied = [c for c in industry.components if c.code in codes]
    else:
        supplied = products
    n_suppliers = min(size["suppliers"], len(supplied)) if request.inbound else 0
    n_overseas = round(n_suppliers * industry.overseas_share)
    if n_suppliers >= 2 and industry.overseas_share >= 0.3:
        n_overseas = max(1, min(n_overseas, n_suppliers - 1))
    overseas = rng.sample(region.overseas, min(n_overseas, len(region.overseas)))
    domestic = rng.sample(region.suppliers, n_suppliers - len(overseas))
    suppliers = [(c, True) for c in overseas] + [(c, False) for c in domestic]
    supplier_ids = {c.name: _slug(c.name, used) for c, _ in suppliers}

    item_id = {p.code: product_ids[p.code] for p in products}
    if produce:
        item_id.update({c.code: f"CMP-{c.code}" for c in supplied})
    # Each supplied item comes from one supplier (round robin over a shuffled list).
    order = list(range(len(suppliers)))
    rng.shuffle(order)
    source = (
        {item.code: suppliers[order[i % len(order)]] for i, item in enumerate(supplied)}
        if (suppliers)
        else {}
    )

    locations: list[dict[str, Any]] = []
    for s in sites:
        locations.append(_loc(site_ids[s.name], site_type, f"{s.name} {industry.site_label}", s))
    for c in customers:
        locations.append(_loc(f"LOC-CUS-{customer_ids[c.name]}", "customer_site", c.name, c))
    for c, _ in suppliers:
        locations.append(_loc(f"LOC-SUP-{supplier_ids[c.name]}", "supplier_site", c.name, c))

    carriers = {"road": "CAR-ROAD", "sea": "CAR-SEA", "air": "CAR-AIR", "rail": "CAR-RAIL"}
    partners: list[dict[str, Any]] = [
        {
            "id": f"CUS-{customer_ids[c.name]}",
            "role": "customer",
            "location_id": f"LOC-CUS-{customer_ids[c.name]}",
            "attributes": {"segment": industry.customer_label},
        }
        for c in customers
    ] + [
        {
            "id": f"SUP-{supplier_ids[c.name]}",
            "role": "supplier",
            "location_id": f"LOC-SUP-{supplier_ids[c.name]}",
            "attributes": {"overseas": is_overseas},
        }
        for c, is_overseas in suppliers
    ]

    lanes: dict[str, dict[str, Any]] = {}

    def lane(origin: City, origin_id: str, dest: City, dest_id: str, mode: str) -> str:
        lane_id = f"LN-{origin_id.split('-')[-1]}-{dest_id.split('-')[-1]}"
        if lane_id not in lanes:
            km = distance_km(origin, dest)
            days = transit_days(km, mode)
            lanes[lane_id] = {
                "id": lane_id,
                "origin_location_id": origin_id,
                "destination_location_id": dest_id,
                "mode": mode,
                "planned_transit_days": days,
                "transit_cv": industry.transit_cv * (0.6 if mode == "air" else 1.0),
                "cost": round(150 + km * {"road": 1.6, "rail": 0.9, "sea": 0.25, "air": 4.5}[mode]),
                "carrier_id": carriers[mode],
            }
        return lane_id

    # Outbound: each customer is served by its nearest site.
    demand: list[dict[str, Any]] = []
    daily: dict[tuple[str, str], float] = {}  # (site id, product code) -> units per day
    for c in customers:
        site = min(sites, key=lambda s: distance_km(s, c))
        sid = site_ids[site.name]
        lane_id = lane(site, sid, c, f"LOC-CUS-{customer_ids[c.name]}", "road")
        picks = rng.sample(products, min(len(products), rng.randint(1, 3)))
        for p in sorted(picks, key=lambda p: p.code):
            rate = round(
                industry.orders_per_customer_day * request.demand_level * rng.uniform(0.5, 1.2), 2
            )
            qty = round(industry.order_quantity * rng.uniform(0.6, 1.5), 1)
            demand.append(
                {
                    "customer_id": f"CUS-{customer_ids[c.name]}",
                    "item_id": item_id[p.code],
                    "ship_from": sid,
                    "lane_id": lane_id,
                    "orders_per_day": rate,
                    "quantity_mean": qty,
                    "quantity_cv": 0.4,
                    "requested_days": round(
                        industry.requested_days + lanes[lane_id]["planned_transit_days"], 1
                    ),
                }
            )
            daily[(sid, p.code)] = daily.get((sid, p.code), 0) + rate * qty

    # Stock, production and inbound per site.
    stock: list[dict[str, Any]] = []
    production: list[dict[str, Any]] = []
    for s in sites:
        sid = site_ids[s.name]
        needs: dict[str, float] = {}  # supplied item code -> units per day
        for p in products:
            d = daily.get((sid, p.code), 0.0)
            if d == 0:
                continue
            if produce:
                stock.append(
                    {
                        "location_id": sid,
                        "item_id": item_id[p.code],
                        "initial": math.ceil(d * 4),
                        "safety_stock": math.ceil(d * 1.5),
                        "reorder_point": 0,
                        "order_up_to": math.ceil(d * 5),
                    }
                )
                production.append(
                    {
                        "location_id": sid,
                        "item_id": item_id[p.code],
                        "components": {item_id[k]: q for k, q in p.components.items()},
                        "rate_per_day": math.ceil(d * 1.4),
                        "batch_days": 1,
                    }
                )
                for comp, q in p.components.items():
                    needs[comp] = needs.get(comp, 0.0) + d * q
            else:
                needs[p.code] = needs.get(p.code, 0.0) + d
        for code, d in sorted(needs.items()):
            stock.append(
                _policy(
                    sid,
                    s,
                    item_id[code],
                    d,
                    code,
                    source,
                    supplier_ids,
                    industry,
                    request,
                    lane,
                    lanes,
                )
            )

    used_items = {s["item_id"] for s in stock}
    items = [
        {
            "id": item_id[p.code],
            "name": p.name,
            "category": p.category,
            "unit": "pcs",
            "weight_kg": p.weight_kg,
            "value": p.value,
        }
        for p in (*products, *(supplied if produce else ()))
        if item_id[p.code] in used_items
    ]
    used_carriers = sorted({ln["carrier_id"] for ln in lanes.values()})
    partners += [
        {"id": c, "role": "carrier", "attributes": {"mode": c.split("-")[1].lower()}}
        for c in used_carriers
    ]

    disruptions = _disruptions(
        rng, request, list(lanes.values()), locations, suppliers, supplier_ids
    )
    region_label = REGIONS[request.region].label
    flows = (
        ["outbound"]
        + (["inbound"] if request.inbound else [])
        + (["production"] if produce else [])
    )
    spec = {
        "name": request.name or request.default_name(),
        "version": "1.0.0",
        "description": (
            f"{industry.label} in {region_label} ({request.size}): {len(sites)} "
            f"{industry.site_label}(s), {len(customers)} customers, {len(suppliers)} suppliers; "
            f"flows: {', '.join(flows)}; disruptions: {request.disruptions}. "
            f"Generated by the SC LiveSim scenario builder v{BUILDER_VERSION}."
        ),
        "horizon_days": request.horizon_days,
        "handling_days": industry.handling_days,
        "locations": locations,
        "partners": partners,
        "items": items,
        "lanes": sorted(lanes.values(), key=lambda ln: ln["id"]),
        "stock": stock,
        "demand": demand,
        "production": production,
        "disruptions": disruptions,
    }
    return ScenarioSpec.model_validate(spec)


def _loc(loc_id: str, type_: str, name: str, city: City) -> dict[str, Any]:
    return {
        "id": loc_id,
        "type": type_,
        "name": name,
        "country": city.country,
        "lat": city.lat,
        "lon": city.lon,
        "attributes": {"city": city.name},
    }


def _policy(
    sid: str,
    site: City,
    item: str,
    d: float,
    code: str,
    source: dict[str, tuple[City, bool]],
    supplier_ids: dict[str, str],
    industry: Industry,
    request: BuilderRequest,
    lane: Any,
    lanes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if not request.inbound or code not in source:
        # No inbound flow: enough stock for the whole horizon, never replenished.
        total = math.ceil(d * request.horizon_days * 1.3) + 1
        return {
            "location_id": sid,
            "item_id": item,
            "initial": total,
            "safety_stock": math.ceil(d * 2),
            "reorder_point": 0,
            "order_up_to": total,
        }
    city, overseas = source[code]
    mode = industry.overseas_mode if overseas else industry.domestic_mode
    sup = supplier_ids[city.name]
    lane_id = lane(city, f"LOC-SUP-{sup}", site, sid, mode)
    lead = 4.0 if overseas else 2.0
    review = 7.0 if mode == "sea" else (2.0 if mode == "air" else 1.0)
    lead_time = lead + lanes[lane_id]["planned_transit_days"]  # processing + transit
    safety = math.ceil(d * max(1.5, 0.3 * lead_time))
    rop = math.ceil(d * (lead_time + review) + safety)
    up_to = math.ceil(rop + d * max(review, 5))
    return {
        "location_id": sid,
        "item_id": item,
        "initial": math.ceil(up_to * 0.85),
        "safety_stock": safety,
        "reorder_point": rop,
        "order_up_to": up_to,
        "review_days": review,
        "supplier_id": f"SUP-{sup}",
        "inbound_lane_id": lane_id,
        "supplier_lead_days": lead,
    }


def _disruptions(
    rng: random.Random,
    request: BuilderRequest,
    lanes: list[dict[str, Any]],
    locations: list[dict[str, Any]],
    suppliers: list[tuple[City, bool]],
    supplier_ids: dict[str, str],
) -> list[dict[str, Any]]:
    profile = DISRUPTION_PROFILES[request.disruptions]
    count = round(profile["per_year"] * request.horizon_days / 365)
    if count == 0:
        return []
    sea = [ln["id"] for ln in lanes if ln["mode"] == "sea"]
    road = [ln["id"] for ln in lanes if ln["mode"] == "road"]
    sites = [loc for loc in locations if loc["type"] in ("warehouse", "plant")]
    supplier_locs = [f"LOC-SUP-{supplier_ids[c.name]}" for c, _ in suppliers]
    candidates: list[tuple[str, str, list[str], tuple[float, float]]] = []
    if sea:
        candidates.append(("port_closure", "lanes", sea, (4, 10)))
    if road:
        candidates.append(("carrier_strike", "lanes", road, (2, 4)))
        candidates.append(("extreme_weather", "lanes", road, (2, 5)))
    if supplier_locs:
        candidates.append(("supplier_outage", "locations", supplier_locs, (5, 14)))
    site_type = "plant_shutdown" if sites and sites[0]["type"] == "plant" else "warehouse_outage"
    candidates.append((site_type, "locations", [s["id"] for s in sites], (1, 3)))
    candidates.append(("demand_spike", "locations", [s["id"] for s in sites], (3, 7)))

    result = []
    starts = sorted(rng.uniform(10, request.horizon_days - 10) for _ in range(count))
    for n, start in enumerate(starts, 1):
        type_, target, pool, (lo, hi) = rng.choice(candidates)
        picked = sorted(rng.sample(pool, min(len(pool), rng.randint(1, 2))))
        result.append(
            {
                "id": f"DIS-GEN-{n:02d}",
                "type": type_,
                "start_day": round(start, 2),
                "duration_days": round(rng.uniform(lo, hi), 1),
                "severity": round(rng.uniform(profile["severity_min"], profile["severity_max"]), 2),
                "lanes": picked if target == "lanes" else [],
                "locations": picked if target == "locations" else [],
            }
        )
    return result


def summarize(spec: ScenarioSpec) -> dict[str, Any]:
    """Key figures of a scenario for previews."""
    modes: dict[str, int] = {}
    for ln in spec.lanes:
        modes[ln.mode] = modes.get(ln.mode, 0) + 1
    types: dict[str, int] = {}
    for loc in spec.locations:
        types[loc.type.value] = types.get(loc.type.value, 0) + 1
    return {
        "name": spec.name,
        "description": spec.description,
        "horizon_days": spec.horizon_days,
        "locations": types,
        "items": len(spec.items),
        "lanes_by_mode": modes,
        "demand_streams": len(spec.demand),
        "expected_orders_per_day": round(sum(d.orders_per_day for d in spec.demand), 1),
        "production_lines": len(spec.production),
        "stock_policies": len(spec.stock),
        "disruptions": [
            {
                "id": d.id,
                "type": d.type,
                "start_day": d.start_day,
                "duration_days": d.duration_days,
                "severity": d.severity,
            }
            for d in spec.disruptions
        ],
    }
