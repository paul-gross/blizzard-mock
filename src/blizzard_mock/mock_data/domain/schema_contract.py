"""The drift guard — the pure comparison between composed ``FactRow``\\ s and a
reflected schema.

Every ``FactRow`` a command composes is checked against the live store's
schema before any insert; a miss fails loud with :class:`SchemaDriftError`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard_mock.mock_data.domain.facts import FactRow

GUIDE = "blizzard-context:/tooling/store-seeding.md"


@dataclass(frozen=True)
class ReflectedColumn:
    """One reflected column's shape — just what the drift guard needs to know."""

    name: str
    nullable: bool
    has_default: bool
    is_autoincrement_pk: bool

    @property
    def is_required(self) -> bool:
        """Whether a ``FactRow`` writing this table must supply this column."""
        return not self.nullable and not self.has_default and not self.is_autoincrement_pk


@dataclass(frozen=True)
class ReflectedTable:
    """One reflected table's shape: its name and its columns, keyed by name."""

    name: str
    columns: Mapping[str, ReflectedColumn]


class SchemaDriftError(Exception):
    """The live store's schema has moved out from under this tool.

    Names the table and offending column(s), so the message alone is actionable.
    """


def require_column(row: Mapping[str, object], column: str, *, table: str) -> object:
    """Read ``column`` off an already-queried ``row``, raising
    :class:`SchemaDriftError` — never a bare ``KeyError`` — when the live
    store no longer carries it. :func:`check_drift` guards the write seam
    only; this guards the read-back path.
    """
    try:
        return row[column]
    except KeyError as exc:
        raise SchemaDriftError(f"schema drift: table {table!r} has no column {column!r} — see {GUIDE}") from exc


def check_drift(schema: Mapping[str, ReflectedTable], rows: Sequence[FactRow]) -> None:
    """Validate ``rows`` against ``schema`` before any of them is inserted.

    Raises :class:`SchemaDriftError` naming the table and offending column(s)
    on the first violation — table presence, then columns, in row order.
    """
    for row in rows:
        table = schema.get(row.table)
        if table is None:
            raise SchemaDriftError(f"schema drift: table {row.table!r} does not exist in the live store — see {GUIDE}")
        unknown = sorted(set(row.values) - set(table.columns))
        if unknown:
            raise SchemaDriftError(f"schema drift: table {row.table!r} has no column(s) {unknown} — see {GUIDE}")
        missing = sorted(
            name for name, column in table.columns.items() if column.is_required and name not in row.values
        )
        if missing:
            raise SchemaDriftError(
                f"schema drift: table {row.table!r} requires column(s) {missing}, not supplied — see {GUIDE}"
            )
