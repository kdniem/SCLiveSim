# ADR 0004: Simulation as a precomputed, re-plannable plan

- Status: accepted
- Date: 2026-10-23

## Context

Phase 2 adds a simulation mode. Consumers must not be able to tell replayed and
simulated runs apart, runs must be reproducible from their seed, and users want
to inject disruptions into running simulations.

## Decision

1. **SimPy produces a plan.** A simulation runs over the whole horizon at run
   start (a year of the templates takes well under a second) and records every
   event with its state changes as a `ReplaySource`, the structure the dataset
   adapters already produce. The replay engine plays it with the same clock,
   pause/resume and exactly-once writes (ADR 0003).
2. **Independent random streams.** Each process draws from its own
   `random.Random` seeded with `"{seed}:{process}"`. Draws happen in simulated
   time order, so anything that changes the model from day d on leaves every
   draw, ID and event before d unchanged.
3. **Injected disruptions re-plan.** The API queues a disruption; the engine
   sets its start to no earlier than the run's current simulated time, re-runs
   the simulation with it and continues at the same cursor. The engine checks
   that the already written prefix is identical and fails the run if not.
   Injected disruptions are stored in the run's options, so a restart rebuilds
   the same plan.
4. **Scenario format** is a Pydantic model (`engine/simulation/spec.py`) with
   cross-reference validation, published as JSON Schema. Scenario files are
   CC BY 4.0, generated data CC0.
5. **Schema 0.2.0** adds the event type `inventory.changed` (initial stock and
   production). It is a backwards-compatible addition.

## Consequences

- Determinism and the "only the future changes" property are cheap to test.
- The plan for long horizons lives in memory (templates: 5,000–17,000 events per
  year). Very large scenarios would need a streaming simulation later.
- Delays have perfect foresight within the model: when a delay becomes known,
  the new ETA is the true arrival. Good enough for test data; noted for later.
