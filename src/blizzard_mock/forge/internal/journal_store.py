"""In-memory request journal (``IRequestJournal``). Process-local, same lifetime as the forge process."""

from __future__ import annotations

from blizzard_mock.forge.domain.journal import JournalEntry


class InMemoryRequestJournal:
    """Process-local list of answered requests. Implements ``IRequestJournal``."""

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []

    def record(self, entry: JournalEntry) -> None:
        self._entries.append(entry)

    def entries(self) -> list[JournalEntry]:
        return list(self._entries)

    def clear(self) -> None:
        self._entries.clear()
