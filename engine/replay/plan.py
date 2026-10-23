"""Data structures that adapters hand to the replay engine.

An adapter turns a dataset into a `ReplaySource`: master data plus a sorted list
of `PlannedEvent`s in *dataset time*. The engine shifts dataset time to simulated
time when it emits an event, so adapters never need to know about runs.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from schema.enums import EventType, ObjectType
from schema.models import Item, Lane, Location, Partner

# A state change written in the same transaction as its event:
# (table name, row without sim_run_id). Datetimes are in dataset time.
StateChange = tuple[str, dict[str, Any]]


@dataclass(frozen=True, slots=True)
class PlannedEvent:
    at: datetime
    """Dataset time (timezone-aware UTC) at which the event happens."""
    rank: int
    """Tie-breaker for events at the same `at` (lower first)."""
    type: EventType
    object_type: ObjectType
    object_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    changes: tuple[StateChange, ...] = ()

    def sort_key(self) -> tuple[datetime, int, str]:
        return (self.at, self.rank, self.object_id)


@dataclass(frozen=True, slots=True)
class ReplaySource:
    scenario: str
    scenario_version: str
    origin: datetime
    """Dataset time that is mapped to the run's `sim_start`."""
    partners: Sequence[Partner]
    items: Sequence[Item]
    events: Sequence[PlannedEvent]
    notes: dict[str, Any] = field(default_factory=dict)
    locations: Sequence[Location] = ()
    lanes: Sequence[Lane] = ()


@dataclass(frozen=True, slots=True)
class SourceOptions:
    """Options a run passes to a source loader."""

    seed: int = 0
    sample_rate: float = 1.0
    dataset_from: datetime | None = None
    dataset_to: datetime | None = None
    disruptions: tuple[dict[str, Any], ...] = ()
    """Disruptions injected into a simulation run (see engine.simulation)."""


SourceLoader = Callable[[SourceOptions], ReplaySource]


def shift(value: Any, offset: timedelta) -> Any:
    """Return `value` with every datetime moved by `offset` (dicts, lists, tuples)."""
    if isinstance(value, datetime):
        return value + offset
    if isinstance(value, dict):
        return {k: shift(v, offset) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return type(value)(shift(v, offset) for v in value)
    return value
