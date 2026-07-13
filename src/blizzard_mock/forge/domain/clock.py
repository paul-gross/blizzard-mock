"""The injected clock seam (``bzh:injected-clock``).

The forge stamps ``created_at`` / ``updated_at`` / ``merged_at`` on issues,
comments, and pull requests. Time flows through this abstraction so tests pin a
fixed instant and assert deterministic timestamps rather than reading the wall
clock. Wired once at the composition root (``forge.app.create_app``).
"""

from __future__ import annotations

from datetime import UTC, datetime
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
        from datetime import timedelta

        self._instant = self._instant + timedelta(seconds=seconds)
