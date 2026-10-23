"""Wall clocks and the mapping between wall-clock and simulated time."""

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """A manually advanced clock for tests."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> None:
        self._now += delta


def sim_now(
    sim_position: datetime, wall_anchor: datetime | None, time_factor: float, wall_now: datetime
) -> datetime:
    """Simulated time now. A run without wall anchor (paused) stands still."""
    if wall_anchor is None:
        return sim_position
    elapsed = max(wall_now - wall_anchor, timedelta(0))
    return sim_position + elapsed * time_factor
