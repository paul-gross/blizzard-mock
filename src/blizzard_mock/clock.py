"""The injected clock seam shared by the mock hub and mock runner (``bzh:injected-clock``).

Time flows through this abstraction so tests pin a fixed instant and assert
deterministic timestamps rather than reading the wall clock. Wired once at each
mock's composition root (``app.create_app``). The mock forge predates this module
and keeps its own equivalent under ``forge.domain.clock``; new mocks share this one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


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
