"""The seeding seam — the store Protocol concept composers write against.

Declares ``ISeedStore`` (``bzh:dependency-inversion``): the store operations
a concept composition needs, owned inward. ``SeedService`` is the ``concept
-> list[FactRow]`` orchestrator concept composers hand their rows to.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard_mock.mock_data.domain.facts import FactRow


@dataclass(frozen=True)
class ResetSummary:
    """What a ``reset`` cleared — the ``reset`` verb's reported shape."""

    rows_deleted: int
    table_count: int


class SeedIntegrityError(Exception):
    """A composed row doesn't satisfy a constraint the drift guard can't see —
    a dangling foreign key, or a unique constraint the store already carries.
    Named actionably rather than surfacing as a raw traceback.
    """


class ISeedStore(Protocol):
    """The store operations a concept composition needs — owned by the domain."""

    def write(self, rows: Sequence[FactRow]) -> None:
        """Validate ``rows`` against the live schema, then insert them
        transactionally, FK-safe (parents before children). Raises
        :class:`SeedIntegrityError` when a row violates a constraint the
        schema drift guard doesn't check (a dangling foreign key, a unique
        clash)."""
        ...

    def reset(self) -> ResetSummary:
        """Delete every row from every table, FK-safe (children before parents)."""
        ...

    def query(self, table: str, where: Mapping[str, object] | None = None) -> list[Mapping[str, object]]:
        """Read ``table``'s rows, filtered by an equality ``where`` (every row when
        ``where`` is falsy) — the read half a concept composer's "reuse if present"
        lookup needs (e.g. ``create chunk --graph <name>`` finding an existing
        minted graph before deciding whether to mint one)."""
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

    def query(self, table: str, where: Mapping[str, object] | None = None) -> list[Mapping[str, object]]:
        """Read a table's rows through the store, for a concept composer's
        existence lookup (see :meth:`ISeedStore.query`)."""
        return self._store.query(table, where)
