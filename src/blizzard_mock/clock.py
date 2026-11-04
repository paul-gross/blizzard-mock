"""The injected clock seam shared by the mock hub and mock runner (``bzh:injected-clock``).

Time flows through this abstraction so tests pin a fixed instant and assert
deterministic timestamps rather than reading the wall clock. Wired once at each
mock's composition root (``app.create_app``).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol

import structlog


class Clock(Protocol):
    """Source of the current instant. Injected everywhere time is read."""

    def now(self) -> datetime: ...


class SystemClock:
    """Production clock — reads the wall clock in UTC. Bound at the root only."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Test clock — returns a pinned instant, advanceable by ``tick``."""

    def __init__(self, instant: datetime) -> None:
        self._instant = instant

    def now(self) -> datetime:
        return self._instant

    def tick(self, seconds: float) -> None:
        self._instant = self._instant + timedelta(seconds=seconds)


class ControlledClock:
    """A frozen, monotonic clock whose moves are atomic."""

    def __init__(self) -> None:
        self._instant = datetime.now(UTC)
        self._lock = Lock()

    def now(self) -> datetime:
        with self._lock:
            return self._instant

    def set(self, to: datetime) -> tuple[datetime, datetime]:
        if to.utcoffset() is None:
            raise ValueError("instant must have a timezone")
        with self._lock:
            previous = self._instant
            if to < previous:
                raise ClockBackwardsError("clock cannot move backwards")
            self._instant = to.astimezone(UTC)
            return previous, self._instant

    def advance(self, by: timedelta) -> tuple[datetime, datetime]:
        if by < timedelta(0):
            raise ValueError("duration cannot be negative")
        with self._lock:
            previous = self._instant
            self._instant += by
            return previous, self._instant


class ClockBackwardsError(ValueError):
    """A requested instant precedes the current clock reading."""


def resolve_clock() -> Clock:
    """Select the controlled clock only when explicitly fenced in."""
    if os.environ.get("BLIZZARD_CLOCK") == "controlled":
        if os.environ.get("BLIZZARD_CLOCK_FENCE") == "1":
            return ControlledClock()
        structlog.get_logger(__name__).warning("BLIZZARD_CLOCK requires BLIZZARD_CLOCK_FENCE=1")
    return SystemClock()
