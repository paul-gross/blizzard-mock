"""The shared lever primitive for the mock hub and mock runner.

A **lever** is an explicit, first-class control an agent or test pulls to steer a mock
into a named edge state instead of contriving it (``implementation/mocking.md``). The
hub and runner mocks share one arm/clear/find store and one ``/_levers`` wire shape
(see ``forge.domain.levers``), each supplying only its own ``kind`` vocabulary and
catalog.

A lever is scoped ``(chunk_id)`` where meaningful; a lever with no chunk is global. A
lever may **self-expire** after ``remaining`` affected requests — the mechanism behind
"go unreachable *mid-lease*": arm ``unreachable`` with ``remaining=2`` and the mock is
down for exactly the next two calls, then heals. ``payload`` carries kind-specific
detail (a delay in milliseconds, a conflicting runner id, a rejection message).
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class Lever(BaseModel):
    """One armed lever: its kind, optional chunk scope, self-expiry, and payload."""

    kind: str
    chunk_id: str | None = None
    #: Auto-clear after this many affected requests; ``None`` means sticky until cleared.
    remaining: int | None = None
    #: Kind-specific detail — ``{"ms": 200}`` for delay, ``{"runner_id": "..."}`` for a
    #: conflicting fact, ``{"message": "..."}`` for a rejection reason.
    payload: dict[str, Any] = Field(default_factory=dict)

    def scope_key(self) -> str:
        return f"{self.kind}#{self.chunk_id or '*'}"

    def matches(self, chunk_id: str | None) -> bool:
        """A global lever matches everything; a scoped lever matches its chunk."""
        return self.chunk_id is None or self.chunk_id == chunk_id


class LeverParams(BaseModel):
    """Request body for arming a lever (POST /_levers/{kind})."""

    chunk_id: str | None = None
    remaining: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ILeverStore(Protocol):
    """Read/write seam over the active lever set (one process-wide store)."""

    def arm(self, lever: Lever) -> None: ...
    def clear(self, kind: str, chunk_id: str | None) -> None: ...
    def clear_all(self) -> None: ...
    def active(self) -> list[Lever]: ...
    def find(self, kind: str, chunk_id: str | None) -> Lever | None: ...
    def consume(self, lever: Lever) -> None:
        """Decrement a self-expiring lever's ``remaining``, clearing it at zero."""
        ...


class InMemoryLeverStore:
    """Process-local active-lever set, keyed by ``(kind, scope)``. Implements ``ILeverStore``."""

    def __init__(self) -> None:
        self._levers: dict[str, Lever] = {}

    def arm(self, lever: Lever) -> None:
        self._levers[lever.scope_key()] = lever

    def clear(self, kind: str, chunk_id: str | None) -> None:
        self._levers.pop(Lever(kind=kind, chunk_id=chunk_id).scope_key(), None)

    def clear_all(self) -> None:
        self._levers.clear()

    def active(self) -> list[Lever]:
        return list(self._levers.values())

    def find(self, kind: str, chunk_id: str | None) -> Lever | None:
        for lever in self._levers.values():
            if lever.kind == kind and lever.matches(chunk_id):
                return lever
        return None

    def consume(self, lever: Lever) -> None:
        if lever.remaining is None:
            return
        lever.remaining -= 1
        if lever.remaining <= 0:
            self._levers.pop(lever.scope_key(), None)
