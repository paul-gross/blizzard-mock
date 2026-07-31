"""The seeding seam — the store Protocol concept composers write against.

The domain declares ``ISeedStore`` (``bzh:dependency-inversion``): the store
operations a concept composition needs, owned inward; ``internal/reflected_store.py``
implements it. ``SeedService`` is the ``concept -> list[FactRow]`` orchestrator
Phase 2+ concept composers hand their rows to; Phase 1 has no composer yet, so
``cli.py`` exercises the same seam directly for ``create runner``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard_mock.mock_data.domain.facts import FactRow


@dataclass(frozen=True)
class ResetSummary:
    """What a ``reset`` cleared — the ``reset`` verb's reported shape."""

    rows_deleted: int
    table_count: int


class ISeedStore(Protocol):
    """The store operations a concept composition needs — owned by the domain."""

    def write(self, rows: Sequence[FactRow]) -> None:
        """Validate ``rows`` against the live schema, then insert them
        transactionally, FK-safe (parents before children)."""
        ...

    def reset(self) -> ResetSummary:
        """Delete every row from every table, FK-safe (children before parents)."""
        ...


class SeedService:
    """Orchestrates ``concept -> list[FactRow]`` composition against a store."""

    def __init__(self, store: ISeedStore) -> None:
        self._store = store

    def seed(self, rows: Sequence[FactRow]) -> None:
        """Hand a composed concept's rows to the store."""
        self._store.write(rows)

    def reset(self) -> ResetSummary:
        """Return the store to a known-clean state."""
        return self._store.reset()
