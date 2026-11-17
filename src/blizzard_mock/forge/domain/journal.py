"""The request journal — every forge request outside the exempt control routes, in arrival order.

A request takes its place in the journal when it arrives and gets its status when it is answered, so a
latency-delayed request keeps its arrival position. A read lists only answered requests. The journal
holds at most ``JOURNAL_CAPACITY`` entries; past that, the oldest is dropped.

A test reads it to assert which requests a client made (and that it made no write), and clears it
before the step it observes.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel


class JournalEntry(BaseModel):
    """One answered request."""

    method: str
    path: str
    status: int


JOURNAL_CAPACITY = 10_000
"""The most entries the journal holds; recording past it drops the oldest."""


class IJournalSlot(Protocol):
    """A request's place in the journal, taken at arrival and answered on completion."""

    def answer(self, status: int) -> None: ...


class IRequestJournal(Protocol):
    """Arrive/read/clear seam over the process-wide journal."""

    def arrive(self, method: str, path: str) -> IJournalSlot: ...
    def entries(self) -> list[JournalEntry]: ...
    def clear(self) -> None: ...
