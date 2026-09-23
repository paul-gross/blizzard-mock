"""Composes one ``garden_proposals`` row, authored rather than delivered
(``blizzard#390``): ``source_artifact_id``/``ref`` land ``NULL``, the shape a
:class:`~blizzard.hub.domain.garden_proposals.GardenProposalAuthoring` mint produces.

Lands no ``garden_proposal_findings`` link rows — this seam exists to compose a
proposal with **no** findings, the case nothing else can seed (``blizzard#543``).
"""

from __future__ import annotations

from dataclasses import dataclass
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow


@dataclass(frozen=True)
class GardenProposalSeed:
    """One composed garden proposal: its minted id and the ``FactRow`` to write."""

    proposal_id: str
    rows: list[FactRow]


def compose_garden_proposal(
    *,
    clock: Clock,
    rng: Random,
    routine_name: str,
    class_: str,
    title: str,
    body: str,
) -> GardenProposalSeed:
    """Compose one garden proposal citing no findings."""
    minted_proposal_id = ids.mint(ids.GARDEN_PROPOSAL_PREFIX, clock, rng)
    row = FactRow(
        table="garden_proposals",
        values={
            "proposal_id": minted_proposal_id,
            "routine_name": routine_name,
            "class": class_,
            "title": title,
            "body": body,
            "created_at": clock.now(),
            "source_artifact_id": None,
            "ref": None,
        },
    )
    return GardenProposalSeed(proposal_id=minted_proposal_id, rows=[row])
