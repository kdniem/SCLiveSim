# ADR 0005: Scenario builder and dataset continuation

- Status: accepted
- Date: 2026-10-23

## Context

The maintainer wants to click together supply chain test data (industry,
region, what to simulate) and to continue existing datasets instead of only
replaying them.

## Decision

1. **Builder = scenario generator.** The builder turns a small request
   (industry, region, size, flows, disruption profile, demand level, horizon,
   seed) into an ordinary `ScenarioSpec`. Libraries of industry profiles
   (products, bills of materials, order sizes, transport habits) and regions
   (real cities with coordinates, overseas sourcing hubs) feed it; transit times
   come from great-circle distances and mode rules of thumb; stock policies are
   sized from the expected demand. Same request → same scenario.
2. **Custom scenarios live in the database** (`scenario` table, migration 0005)
   as validated specs, so the API and the engine (separate processes) share them
   and runs stay reproducible. They can be downloaded as YAML, edited and
   uploaded.
3. **Continuation = calibration mode.** A continuation run concatenates two
   plans: the dataset replay up to the first midnight after its last order, and
   a simulation calibrated on the dataset, shifted to start there. Simulated IDs
   carry a namespace (`ORD-C…`). The run's mode is `calibration`.
4. **Calibration only estimates what the data contains** (order rates per region,
   shipping class and product, quantities, transit ratio and variability per
   shipping class). Missing parts use explicit placeholders: a DC without
   coordinates, `road` with the real shipping class as `service`, and
   `unlimited` stock that emits no inventory data.

## Consequences

- Builder output is plausible test data, not market data; profiles are easy to
  extend in `engine/simulation/library.py`.
- The scenario format gains `LaneSpec.service` and `StockPolicy.unlimited`
  (spec 0.1.0, backwards compatible).
- Disruptions cannot yet be injected into continuation runs (the engine would
  need to re-plan only the simulated part); the API answers 409.
