"""``FactRow`` — one row destined for one named table.

The domain vocabulary this tool composes in (``bzh:domain-core``): a concept
composer turns a seedable concept into ``list[FactRow]``, never a
status column — the hub derives status from facts, so this tool only ever
composes fact sets (``bzh:facts-not-status``). Naming a table is a deliberate,
bounded exception to keeping the domain schema-agnostic: this tool's whole
contract is writing schema-faithful rows into someone else's schema, so the
table name *is* domain vocabulary here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class FactRow:
    """One row destined for one named table: ``table`` plus its column values."""

    table: str
    values: Mapping[str, object]
