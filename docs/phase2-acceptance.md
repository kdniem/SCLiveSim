# Phase 2 acceptance (v0.2.0)

Gate from `CLAUDE.md`: *both templates run for 90 simulated days without
crashes, same seed → identical event stream (tested), an injected disruption
changes only events after its start.*

## 1. Automated tests (run in CI on every push)

| Test | What it proves |
| --- | --- |
| `tests/test_simulation_runs.py::test_90_days_and_same_seed[wholesale/manufacturing]` | Engine + database: 90 simulated days per template, runs stay `running`, two runs with the same seed have identical event rows (SHA-256), inventory never negative |
| `tests/test_simulation.py::test_same_seed_same_plan` | Same seed → same plan, different seed → different plan |
| `tests/test_simulation.py::test_injected_disruption_changes_only_the_future` | Every event before the disruption start is identical; later events differ; delays name the disruption as `cause` |
| `tests/test_simulation_runs.py::test_injected_disruption_via_api` | The same through `POST /runs/{id}/disruptions` on a running run, compared with an untouched twin run |
| `tests/test_builder.py` | Every industry × region builds and simulates; flow switches and disruption profiles work; save, download, upload and run a built scenario |
| `tests/test_continuation.py` | Calibration matches the data; replay then simulation with no ID collisions; continuation runs have mode `calibration` |

## 2. Live run (2026-10-23, `docker compose up` on v0.2.0)

`wholesale` (RUN-0001) and `manufacturing` (RUN-0002), seed 7, time factor
20,000, ran side by side for 8 minutes of wall time ≈ 103 simulated days. The
control tower stayed open on RUN-0001 in headless Chromium, sampled every 15 s.
After two minutes a `port_closure` (severity 0.9, 10 days) was injected into
RUN-0001 through the API.

| Check | Result |
| --- | --- |
| Simulated time covered | 2026-10-23 → 2027-02-03 (103 days) for both runs |
| Run status | both `running`, no `failed` |
| Stream connection | `live` in 28 of 28 samples |
| Browser console errors | none |
| Engine, API and webhook worker log errors | none |
| Events | wholesale 4,890, manufacturing 1,425 |
| Inventory | 4 rows per run, never negative (minimum on hand: 211 and 135) |
| Injected disruption | `DIS-INJ-001` started on the next tick, ended after 10 days; both sea shipments in transit got `shipment.delayed` with `cause: DIS-INJ-001` |
| Scheduled disruptions | port closure, demand spike (open shipments rose from ~15 to 34), supplier outage, carrier strike: all started and ended; the port closure hit the already delayed sea shipments as `shipment.eta_changed` (+13.8 days in total) |
| Memory | engine 111 MB, API 53 MB, webhook worker 45 MB, PostgreSQL 78 MB |

## 3. Builder and continuation (manual, same day)

- Scenario builder in the browser: automotive parts, North America, medium,
  medium disruptions → preview (2 plants, 8 customers, 3 suppliers, 14 lanes,
  map across the Pacific) → *Save & start run* → control tower opened the new
  run and showed live shipments and named inventory. No console errors.
- *Replay & continue* (DataCo): the last replayed order arrived at 12:57, the
  first simulated order (`ORD-C000001`) at 13:26, the hand-over at 13:19
  simulated time; shipping class mix and order level continued.
