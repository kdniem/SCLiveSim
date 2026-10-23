"""Continue a dataset: replay its end, then simulate on from a calibrated scenario.

A continuation run replays the dataset from `dataset_from` (default: the last
30 days) and, from the first midnight after the dataset's last order, plays a
simulation calibrated on the dataset. Both parts form one plan, so the run
looks like one uninterrupted stream to consumers. Simulated IDs carry a `C`
(e.g. `ORD-C000001`) so they never collide with replayed ones.
"""

from dataclasses import replace
from datetime import datetime, timedelta

from adapters.dataco.calibrate import calibrate
from adapters.dataco.mapping import DataCoLoader, build_source
from engine.replay.plan import PlannedEvent, ReplaySource, SourceOptions, shift
from engine.simulation.model import ORIGIN, Simulation, core_lanes
from engine.simulation.spec import ScenarioSpec

DEFAULT_REPLAY_DAYS = 30
CALIBRATION_WINDOW_DAYS = 90  # recent level, so the hand-over is seamless


class DataCoContinuation:
    def __init__(self, dataset: DataCoLoader, horizon_days: int = 365) -> None:
        self.dataset = dataset
        self.horizon_days = horizon_days
        self._spec: ScenarioSpec | None = None

    def spec(self) -> ScenarioSpec:
        if self._spec is None:
            self._spec = calibrate(
                self.dataset.parsed(),
                name="dataco-continued",
                window_days=CALIBRATION_WINDOW_DAYS,
                horizon_days=self.horizon_days,
            )
        return self._spec

    def handover(self) -> datetime:
        """First midnight after the dataset's last order: the simulation starts here."""
        end = max(o.order_date for o in self.dataset.parsed().orders.values())
        return end.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    def __call__(self, options: SourceOptions) -> ReplaySource:
        handover = self.handover()
        start = options.dataset_from or handover - timedelta(days=DEFAULT_REPLAY_DAYS)
        replay = build_source(
            self.dataset.parsed(),
            SourceOptions(seed=options.seed, sample_rate=options.sample_rate,
                          dataset_from=start, dataset_to=handover),
        )  # fmt: skip
        spec = self.spec()
        simulated = Simulation(spec, options.seed, id_namespace="C").run()
        offset = handover - ORIGIN
        moved = [
            replace(e, at=e.at + offset, payload=shift(e.payload, offset),
                    changes=shift(e.changes, offset))
            for e in simulated
        ]  # fmt: skip
        events: list[PlannedEvent] = sorted([*replay.events, *moved], key=PlannedEvent.sort_key)
        items = {i.id: i for i in (*replay.items, *spec.items)}
        return ReplaySource(
            scenario="dataco-continued",
            scenario_version=spec.version,
            origin=replay.origin,
            partners=[*replay.partners, *spec.partners],
            items=[items[k] for k in sorted(items)],
            events=events,
            locations=spec.locations,
            lanes=core_lanes(spec),
            notes={**replay.notes, "handover": handover.isoformat(),
                   "calibration": spec.description},
        )  # fmt: skip
