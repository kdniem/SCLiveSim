# Datasets

Raw datasets are never committed. They live in `data/raw/` (gitignored).

## DataCo Smart Supply Chain for Big Data Analysis

| | |
| --- | --- |
| Source | Mendeley Data, <https://doi.org/10.17632/8gx2fvg2k6.5> |
| Authors | Fabian Constante, Fernando Silva, António Pereira (2019) |
| Version | 5 |
| License | CC BY 4.0 (checked 2026-10-23) |
| File used | `DataCoSupplyChainDataset.csv` (96 MB, SHA-256 `fa6d022e…0aa6`) |

Download it (checksums are verified):

```bash
uv run python -m adapters.dataco.fetch      # → data/raw/
```

With Docker Compose the engine downloads it automatically on first start
(set `DATACO_AUTO_DOWNLOAD=false` to disable).

### What the data contains

Verified against the real CSV: 180,519 order lines in 65,752 orders,
2015-01-01 to 2018-01-31, about 58 orders per day. One row per order line;
shipping mode, delivery status and dates are identical for all lines of an order.

- Outbound customer orders only. No inbound, no inventory, no origin location,
  no carrier. `Shipping Mode` is a service class (Standard, Second, First, Same Day).
- `shipping date (DateOrders)` = order date + `Days for shipping (real)`
  (±12 hours in 5% of orders). It is effectively the **delivery** time.
- `Delivery Status`: Advance shipping, Shipping on time, Late delivery
  (real > scheduled days), Shipping canceled (order status CANCELED or SUSPECTED_FRAUD).
- Timestamps have no time zone; SC LiveSim treats them as UTC.

### Mapping to the core schema

| Core entity | From | ID |
| --- | --- | --- |
| `partner` (customer) | `Order Customer Id`, `Customer Segment` → `attributes.segment` | `CUS-01234` |
| `item` | `Product Card Id`, name, category, price, department → `attributes` | `ITM-0365` |
| `order` (outbound) | `Order Id`, order date; `requested_date` = order date + scheduled days | `ORD-077202` |
| `order_line` | `Order Item Id`, quantity, `Order Item Total` as value | `OL-180517` |
| `shipment` (one per order) | dates and status below | `SHP-077202` |

`order.attributes` holds `shipping_mode`, `destination` (market, region, country,
state, city), `source_order_status` and `source_delivery_status`.

Events per order (dataset time → simulated time by a constant shift):

| When | Event | State |
| --- | --- | --- |
| order date | `order.created` | order + lines |
| order date | `shipment.departed` | shipment `in_transit`, `etd = atd` = order date, `eta_planned` = order date + scheduled days; order `shipped` |
| `eta_planned` (late orders only) | `shipment.delayed` | shipment `delayed`, `eta_current` = actual delivery |
| shipping date | `shipment.delivered` | shipment and order `delivered` |

Cancelled orders get `order.created` with status `cancelled` and no shipment.
Full dataset: 227,594 events (65,752 created, 62,897 departed, 36,048 delayed,
62,897 delivered).

### Assumptions (no data is invented)

- **Departure = order date.** The data has no dispatch time; shipping days count
  from the order date.
- **Delay becomes known at the planned ETA**, and the new ETA is the actual
  delivery time. Real systems learn about delays earlier or later; the dataset
  does not say.
- **No lanes, locations or carriers** are created, because the dataset has no
  origin or carrier. `lane_id` and `carrier_id` stay empty.
- **Inventory is empty.** `GET /inventory` says so in its response header.
- `order.confirmed`, `shipment.picked_up` and `shipment.eta_changed` are not emitted.

### Privacy

The CSV contains customer names, street addresses, zip codes, coordinates and
masked e-mails and passwords. The adapter reads none of these columns; customers
only appear as pseudonymous `CUS-…` IDs with their segment.

### Run options

- `seed` + `sample_rate` (0–1]: seeded sample of orders. Same seed, same sample.
- `dataset_from` / `dataset_to`: replay only a window of the dataset.
  The run's `sim_start` corresponds to `dataset_from` (default 2015-01-01 00:00).

## Continuing DataCo (calibration)

`dataco-continued` replays the last 30 days of DataCo and continues from the
first midnight after its last order (2018-02-01) with a simulation calibrated on
the last 90 days. `POST /scenarios/calibrate` saves such a calibrated scenario
(any window) to run or edit on its own. Code: `adapters/dataco/calibrate.py`,
`engine/continuation.py`, ADR 0005.

| Estimated from the data | How |
| --- | --- |
| Order rate per destination region × shipping class × product | Orders in the window / days, Poisson arrivals |
| Order quantity | Mean and coefficient of variation per stream (main line of each order) |
| Planned transit | `Days for shipment (scheduled)` per shipping class (Same Day: 0.25 days) |
| Actual transit | Ratio of real to scheduled days and its variability per shipping class |

Checked on the real data (seed 1): the simulation continues at the dataset's
recent level (about 65 orders per day), with the same shipping class mix and a
late-delivery share of 64 % (DataCo: 57 %).

Placeholders, because DataCo does not contain the information:

- **Customers** are aggregated per destination region (`CUS-REG-…`).
- **Origin**: one DC `LOC-DC-DATACO` without coordinates.
- **Transport mode**: `road` as a placeholder; the real shipping class is kept as
  the lane's `service` and appears as `shipping_mode` in events.
- **Inventory**: `unlimited` stock, so no inventory rows or events are produced.
- **Order lines**: one line per simulated order (the order's highest-value line),
  so order counts match the data while item volume per order is lower.
- **Cancellations** (about 4 % of orders) are not simulated.
- Products outside the top 20 are folded into the others' rates.
