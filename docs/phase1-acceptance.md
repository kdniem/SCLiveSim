# Phase 1 acceptance (v0.1.0)

Gate from `CLAUDE.md`: *the control tower example runs against a replay for one
week without crashes, and a second run with the same seed produces an identical
event stream (covered by a test).*

## 1. Identical event stream for the same seed

`tests/test_replay.py::test_same_seed_gives_identical_event_stream` creates three
runs on the same dataset fixture (seed 42, seed 42, seed 7; `sample_rate` 0.7),
replays them to the end and compares SHA-256 digests of all event rows
(`id, ts, type, object_type, object_id, payload`). Runs 1 and 2 are identical;
run 3 differs. The test runs in CI on every push.

## 2. One simulated week without crashes

Two checks, both on the real DataCo dataset (V5, 65,752 orders):

**Automated** (`tests/test_soak_dataco.py`, runs wherever the dataset is in
`data/raw/`): one simulated week at 1440× with a fake clock (840 ticks of 0.5 s),
then API checks: run still `running`, more than 1,000 events, all in time order
and inside the week, open shipments have no arrival time, every delayed shipment
has `eta_current > eta_planned`, every delivered shipment has an arrival time.

**Live** (2026-10-23, `docker compose up` on the commit that added this file):
the control tower page stayed open in headless Chromium for 7.5 minutes of wall
time = 7.5 simulated days at 1440×, sampled every 15 seconds.

| Check | Result |
| --- | --- |
| Simulated time covered | 2026-10-26 11:03 → 2026-11-02 17:15 (7.3 days), plus 2.5 days before the browser attached |
| Stream connection | `live` in all 30 samples, 0 reconnects |
| Browser console errors | none |
| Engine and API log errors | none |
| Page state vs. API at the end | 201 open / 52 delayed on the page, 201 / 52 from `GET /shipments` |
| Open shipments over the week | stable between 144 and 219; delayed 14–68 |
| Memory | engine 535 MB (constant), API 49 MB, PostgreSQL 47 MB |

Afterwards the engine container was restarted: the run continued at its stored
cursor with no duplicate or missing events (2,260 events, 2,260 distinct IDs).

## 3. Quickstart

From a clean checkout, `docker compose up -d` builds the image (about 1–2 minutes),
downloads the dataset (96 MB, checksum verified), loads it (about 15 s) and
starts streaming. The control tower at <http://localhost:8080> shows the first
shipments immediately and the first delays after one to four minutes.
