"""Composes one garden proposal's ``FactRow`` set — the ``garden_proposals``/
``garden_proposal_closures`` trail (``bzh:facts-not-status``). A proposal is open while
no ``garden_proposal_closures`` row exists for it; ``--closure`` lands one, a pass or an
acceptance split on whether the accepted item was minted or declined."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow

CLOSURE_PASSED = "passed"
CLOSURE_ACCEPTED_MINTED = "accepted-minted"
CLOSURE_ACCEPTED_DECLINED = "accepted-declined"

#: The three ``--closure`` values :func:`compose_garden_proposal` accepts; ``None`` is open.
CLOSURES = (CLOSURE_PASSED, CLOSURE_ACCEPTED_MINTED, CLOSURE_ACCEPTED_DECLINED)

#: A ``--closure`` value's landed ``garden_proposal_closures`` ``(closure, item_outcome)``.
_CLOSURE_COLUMNS: dict[str, tuple[str, str | None]] = {
    CLOSURE_PASSED: ("passed", None),
    CLOSURE_ACCEPTED_MINTED: ("accepted", "minted"),
    CLOSURE_ACCEPTED_DECLINED: ("accepted", "declined"),
}

#: ``closed_by``'s default when a seeded closure doesn't name an operator.
DEFAULT_CLOSED_BY = "seed-operator"


class GardenProposalCompositionError(Exception):
    """A ``--closure`` :func:`compose_garden_proposal` cannot honor."""


@dataclass(frozen=True)
class GardenProposalSeed:
    """One composed garden proposal: its minted id and the exact ``FactRow``\\ s to write."""

    proposal_id: str
    rows: list[FactRow] = field(default_factory=list)


def compose_garden_proposal(
    *,
    clock: Clock,
    rng: Random,
    routine_name: str,
    class_: str,
    title: str | None = None,
    body: str | None = None,
    created_at: datetime | None = None,
    closure: str | None = None,
    closed_by: str = DEFAULT_CLOSED_BY,
) -> GardenProposalSeed:
    """Compose one garden proposal, optionally already closed. ``title``/``body``
    default to boilerplate; ``created_at`` defaults to now, but an explicit value seeds
    outside the current instant. ``closure`` is one of :data:`CLOSURES`, or ``None`` for
    an open proposal; an unknown value raises :class:`GardenProposalCompositionError`.
    No already-seeded chunk or routine is required — ``routine_name`` is a plain string."""
    if closure is not None and closure not in CLOSURES:
        raise GardenProposalCompositionError(f"unknown closure {closure!r} — one of {CLOSURES}")

    minted_proposal_id = ids.mint(ids.GARDEN_PROPOSAL_PREFIX, clock, rng)
    landed_at = created_at if created_at is not None else clock.now()
    rows: list[FactRow] = [
        FactRow(
            table="garden_proposals",
            values={
                "proposal_id": minted_proposal_id,
                "routine_name": routine_name,
                "class": class_,
                "title": title if title is not None else f"{routine_name}: {class_}",
                "body": body if body is not None else f"Garden proposal for {routine_name!r}, class {class_!r}.",
                "created_at": landed_at,
                "source_artifact_id": None,
                "ref": None,
            },
        )
    ]
    if closure is not None:
        closure_value, item_outcome = _CLOSURE_COLUMNS[closure]
        rows.append(
            FactRow(
                table="garden_proposal_closures",
                values={
                    "proposal_id": minted_proposal_id,
                    "closure": closure_value,
                    "reason": None,
                    "closed_by": closed_by,
                    "closed_at": landed_at + timedelta(seconds=1),
                    "item_outcome": item_outcome,
                    "source": None,
                    "ref": None,
                },
            )
        )
    return GardenProposalSeed(proposal_id=minted_proposal_id, rows=rows)
