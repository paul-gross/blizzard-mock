"""Compose a hub lease and its epoch owner (``bzh:facts-not-status``).

Running and delivering chunks share these rows; without an owner, the seeded
runner cannot write at that epoch.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

#: The ``lease_facts.runner_id`` of a hub mint — whose epoch the hub owns (``runner_id`` NULL).
HUB_RUNNER_ID = "hub"


def compose_lease_row(*, chunk_id: str, epoch: int, runner_id: str, minted_at: datetime) -> FactRow:
    """One ``lease_facts`` row — a chunk's lease mint at ``epoch``, held by ``runner_id``."""
    return FactRow(
        table="lease_facts",
        values={"chunk_id": chunk_id, "epoch": epoch, "runner_id": runner_id, "minted_at": minted_at},
    )


def compose_epoch_owner_row(*, chunk_id: str, epoch: int, runner_id: str, recorded_at: datetime) -> FactRow:
    """One epoch owner; :data:`HUB_RUNNER_ID` denotes the hub."""
    return FactRow(
        table="epoch_owners",
        values={
            "chunk_id": chunk_id,
            "epoch": epoch,
            "runner_id": None if runner_id == HUB_RUNNER_ID else runner_id,
            "recorded_at": recorded_at,
        },
    )


def compose_lease_rows(*, chunk_id: str, epoch: int, runner_id: str, minted_at: datetime) -> list[FactRow]:
    """A lease mint taking an unowned epoch: its ``lease_facts`` row and the owner row."""
    return [
        compose_lease_row(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=minted_at),
        compose_epoch_owner_row(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, recorded_at=minted_at),
    ]
