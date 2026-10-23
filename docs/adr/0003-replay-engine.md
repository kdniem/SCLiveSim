# ADR 0003: Replay engine design

- Status: accepted
- Date: 2026-10-23

## Context

Phase 1 replays a public dataset as a live stream. Runs need a time factor,
a seed, pause/resume, and the same seed must give the same event stream.

## Decision

1. **Adapters produce a plan, the engine plays it.** An adapter turns a dataset
   into a `ReplaySource`: master data plus a sorted list of `PlannedEvent`s in
   dataset time, each with the state changes it causes. The engine never needs
   dataset knowledge, and simulation (Phase 2) can produce the same structure.
2. **Time mapping.** `sim_time = sim_start + (dataset_time - origin)`. By default
   `sim_start` is the wall-clock time when the run is created, so the dataset
   "starts now". Simulated time advances as
   `sim_position + (wall_now - wall_anchor) * time_factor`.
3. **Pause/resume** freezes `sim_position` and clears `wall_anchor`; resume sets a
   new anchor. Paused wall time does not advance simulated time.
4. **Control via the database.** The API writes `run_control.desired_status`; the
   engine applies it on its next tick (default every 0.5 s). The engine is the
   only writer of events and state, as ADR 0001 requires.
5. **Exactly-once writes.** Each tick writes due events, their state changes and
   the new cursor in one transaction per run. After a restart the engine rebuilds
   the plan (deterministic) and continues at the stored cursor.
6. **Determinism.** Event order is `(time, rank, object_id)`; event IDs are the
   position in the plan (`EVT-00000001`); payloads are serialized with sorted
   keys. Same scenario version + seed + options + `sim_start` + engine version
   → byte-identical event rows (`tests/test_replay.py`). With the default
   `sim_start` (now), streams differ only by the constant time shift.
7. **The seed** controls order sampling (`sample_rate`). With `sample_rate = 1`,
   a replay is fully determined by the dataset.

## Consequences

- The whole plan is held in memory per run (about 0.5 GB for the full DataCo
  dataset). Acceptable for Phase 1; a streaming plan can replace it later.
- Pause/resume latency is one tick.
- `run_control` is engine-internal and not part of the public core schema.
