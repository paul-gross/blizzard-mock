"""Composes one runner-store ``takeovers`` row, open (no ``takeover_ends`` sibling).

Mints its own ``tko_<ulid>`` id (``bzh:domain-core``). ``fence_epoch`` is ``NULL``
when no live worker was force-killed to open it — the ``needs_human`` mirror's shape.
"""

from __future__ import annotations

import random
from datetime import datetime

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow


def compose_takeover(
    *,
    chunk_id: str,
    lease_id: str | None,
    session_id: str | None,
    workdir: str,
    fence_epoch: int | None,
    opened_at: datetime,
    clock: Clock,
    rng: random.Random,
) -> FactRow:
    """One open ``takeovers`` row."""
    return FactRow(
        table="takeovers",
        values={
            "takeover_id": ids.mint(ids.TAKEOVER_PREFIX, clock, rng),
            "chunk_id": chunk_id,
            "lease_id": lease_id,
            "session_id": session_id,
            "workdir": workdir,
            "fence_epoch": fence_epoch,
            "opened_at": opened_at,
        },
    )
