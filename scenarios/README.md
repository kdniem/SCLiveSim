# Scenarios

A scenario describes a supply chain network the simulation engine can run:
locations, partners, items, lanes, stock policies, demand, production and
scheduled disruptions. One folder per scenario, each with a `scenario.yaml` and a
`README.md`. Scenario files are licensed under **CC BY 4.0**; data generated from
them is **CC0**.

| Scenario | What it shows |
| --- | --- |
| [`wholesale`](wholesale/README.md) | Distribution centre with sea and road inbound, retail outbound, port closure, demand spike |
| [`manufacturing`](manufacturing/README.md) | Plant with bill of materials, three suppliers, supplier outage, plant shutdown |

Run one:

```bash
curl -X POST http://localhost:8000/runs -H 'content-type: application/json' \
     -d '{"scenario": "wholesale", "seed": 1, "time_factor": 1440}'
```

## Format

The format is defined by `engine/simulation/spec.py` and published as JSON Schema
in [`scenario.schema.json`](scenario.schema.json) (editors such as VS Code can
validate YAML against it). All durations are in days.

| Section | Content |
| --- | --- |
| `locations`, `partners`, `items` | Core schema master data |
| `lanes` | Transport lanes: planned transit days, `transit_cv` (variability), `transit_mean_ratio` (actual / planned on average, default 0.9), carrier |
| `stock` | (s, S) policy per location and item: `initial`, `safety_stock`, `reorder_point`, `order_up_to`, `review_days`, supplier and inbound lane |
| `demand` | Customer order streams: Poisson `orders_per_day`, lognormal quantity, lane, requested delivery days |
| `production` | Bill of materials, `rate_per_day`, `batch_days`; output fills up to `order_up_to` |
| `disruptions` | Scheduled disruptions from the catalog below |

## How the simulation behaves

- Customer orders reserve stock first come, first served (`order.confirmed`);
  without stock they wait as backorders.
- After `handling_days` a shipment departs, unless its site has an outage.
- Transit time is random around the lane's plan. Delays become visible when a
  disruption hits the lane (`cause` = disruption ID) or when the planned ETA
  passes (`cause` = `transit_time`): first `shipment.delayed`, then
  `shipment.eta_changed`.
- Stock policies are reviewed every `review_days`; purchase orders (`PO-…`,
  direction `inbound`) travel over the inbound lane and refill stock on arrival.
- `inventory.below_safety_stock` fires when on-hand stock falls below safety stock.
- Production consumes components and emits `inventory.changed`.

## Disruption catalog

| Type | Effect | Targets |
| --- | --- | --- |
| `port_closure`, `carrier_strike`, `extreme_weather` | `lane_delay`: shipments in transit during the window or departing in it arrive `severity × duration` days later | lanes |
| `supplier_outage`, `warehouse_outage`, `plant_shutdown` | `site_outage`: no departures or production during the window (severity < 1: partial) | locations |
| `demand_spike` | demand served from the location × (1 + 3 × severity) | locations |

Disruptions can also be injected into a running simulation:

```bash
curl -X POST http://localhost:8000/runs/RUN-0002/disruptions -H 'content-type: application/json' \
     -d '{"type": "port_closure", "lanes": ["LN-SZX-HAM"], "duration_days": 10, "severity": 0.9}'
```

The engine re-plans the run from the current simulated time; everything that
already happened stays exactly the same.

## Writing your own

Copy a folder, change `name`, and validate it:

```bash
uv run python -c "from pathlib import Path; from engine.simulation.spec import load_scenario; \
print(load_scenario(Path('scenarios/my-scenario/scenario.yaml')).name)"
```

The engine picks up every `scenarios/*/scenario.yaml` at start.
