"""Disruption catalog: named disruption types and how they act on the network.

Three effect kinds cover the catalog:

- `lane_delay`: shipments on the affected lanes that are in transit during the
  window, or depart during it, arrive later by `severity * duration_days`.
- `site_outage`: affected locations cannot ship or produce during the window;
  work resumes when it ends. With severity < 1, capacity is reduced instead
  (production rate times `1 - severity`; shipments wait with probability `severity`).
- `demand_spike`: demand served from the affected locations is multiplied by
  `1 + 3 * severity` during the window.
"""

from dataclasses import dataclass
from typing import Literal

EffectKind = Literal["lane_delay", "site_outage", "demand_spike"]


@dataclass(frozen=True)
class DisruptionType:
    name: str
    effect: EffectKind
    targets: Literal["lanes", "locations"]
    description: str


CATALOG: dict[str, DisruptionType] = {
    t.name: t
    for t in (
        DisruptionType(
            "port_closure", "lane_delay", "lanes", "A port closes; sea lanes through it stall."
        ),
        DisruptionType(
            "carrier_strike", "lane_delay", "lanes", "A carrier strike slows road or rail lanes."
        ),
        DisruptionType(
            "extreme_weather", "lane_delay", "lanes", "Storms, floods or snow delay transport."
        ),
        DisruptionType(
            "supplier_outage", "site_outage", "locations", "A supplier site stops shipping."
        ),
        DisruptionType(
            "warehouse_outage",
            "site_outage",
            "locations",
            "A warehouse cannot ship (fire, IT outage, labour shortage).",
        ),
        DisruptionType(
            "plant_shutdown", "site_outage", "locations", "A plant stops production and shipping."
        ),
        DisruptionType(
            "demand_spike",
            "demand_spike",
            "locations",
            "Demand served from a location jumps (promotion, panic buying).",
        ),
    )
}
