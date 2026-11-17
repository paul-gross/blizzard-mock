"""In-memory request journal (``IRequestJournal``). Process-local, same lifetime as the forge process."""

from __future__ import annotations

from collections import deque

from blizzard_mock.forge.domain.journal import JOURNAL_CAPACITY, JournalEntry


class _Slot:
    """A request's arrival-ordered place; ``status`` stays ``None`` until it is answered."""

    def __init__(self, method: str, path: str) -> None:
        self.method = method
        self.path = path
        self.status: int | None = None

    def answer(self, status: int) -> None:
        self.status = status


class InMemoryRequestJournal:
    """Process-local, bounded queue of requests in arrival order. Implements ``IRequestJournal``."""

    def __init__(self, capacity: int = JOURNAL_CAPACITY) -> None:
        self._slots: deque[_Slot] = deque(maxlen=capacity)

    def arrive(self, method: str, path: str) -> _Slot:
        slot = _Slot(method, path)
        self._slots.append(slot)
        return slot

    def entries(self) -> list[JournalEntry]:
        return [
            JournalEntry(method=s.method, path=s.path, status=s.status)
            for s in list(self._slots)
            if s.status is not None
        ]

    def clear(self) -> None:
        self._slots.clear()
