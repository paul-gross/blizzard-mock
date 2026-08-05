"""Composes one ``lease_facts`` row (``bzh:facts-not-status``).

The single fact ``running``/``delivering`` chunks share a shape with — this
module is the one place the row shape lives, composed once and reused rather
than re-derived.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow


def compose_lease_row(*, chunk_id: str, epoch: int, runner_id: str, minted_at: datetime) -> FactRow:
    """One ``lease_facts`` row — a chunk's lease mint at ``epoch``, held by ``runner_id``."""
    return FactRow(
        table="lease_facts",
        values={"chunk_id": chunk_id, "epoch": epoch, "runner_id": runner_id, "minted_at": minted_at},
    )
