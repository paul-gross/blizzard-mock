"""Composes one runner-store ``outbound_buffer`` row — the panel's Facts section.

Mirrors the hub-bound fact kinds independently (no ``blizzard`` import), the same
precedent ``domain/ids.py`` sets. ``scenario_seed`` composes these pre-acked, so a
seeded fleet renders without a live hub round-trip.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

# The fact kinds this buffer carries — mirrored from ``blizzard.wire.facts``.
QUESTION_ASKED = "question.asked"
ESCALATION_RECORDED = "escalation.recorded"


def compose_outbound_fact(
    *,
    kind: str,
    chunk_id: str | None,
    lease_id: str | None,
    payload: Mapping[str, object],
    created_at: datetime,
    acked_at: datetime | None = None,
) -> FactRow:
    """One ``outbound_buffer`` row, pre-acked when ``acked_at`` is given."""
    return FactRow(
        table="outbound_buffer",
        values={
            "kind": kind,
            "chunk_id": chunk_id,
            "lease_id": lease_id,
            "payload": json.dumps(payload),
            "created_at": created_at,
            "acked_at": acked_at,
        },
    )
