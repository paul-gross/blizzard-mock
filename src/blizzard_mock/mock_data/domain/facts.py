"""``FactRow`` — one row destined for one named table.

The domain vocabulary this tool composes in (``bzh:domain-core``): a concept
composer turns a seedable concept into ``list[FactRow]``, never a status
column (``bzh:facts-not-status``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class FactRow:
    """One row destined for one named table: ``table`` plus its column values."""

    table: str
    values: Mapping[str, object]
