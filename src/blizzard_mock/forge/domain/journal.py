"""The request journal — every forge request outside the exempt control routes, in arrival order.

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


class IRequestJournal(Protocol):
    """Append/read/clear seam over the process-wide journal."""

    def record(self, entry: JournalEntry) -> None: ...
    def entries(self) -> list[JournalEntry]: ...
    def clear(self) -> None: ...
