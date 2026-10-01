"""Composes a hub lease mint — one ``lease_facts`` row and its epoch's ``epoch_owners`` row
(``bzh:facts-not-status``).

The shape ``running``/``delivering`` chunks share — this module is the one place the row
shapes live, composed once and reused rather than re-derived. The hub admits a runner's
write only at an epoch that runner owns, so a seeded lease without its owner row would
leave a chunk no runner can drive.
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
    """One ``epoch_owners`` row — ``runner_id`` owns the chunk's ``epoch``; the hub for
    :data:`HUB_RUNNER_ID`. The store keeps one owner per epoch."""
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
