"""Composes one ``chunk_bounces`` row (``bzh:facts-not-status``).

A bounce is a coordinator kick-back of one attempt: its ``cause`` is one of the
hub schema's three, and its ``envelope`` is the raw JSON kick-back payload,
stored as text exactly as the hub stores it.
"""

from __future__ import annotations

import json
from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

CAUSE_CONFLICT = "conflict"
CAUSE_CHECKS = "checks"
CAUSE_MASTER_MOVED = "master-moved"

#: The causes ``compose_bounce`` accepts — the hub schema's ``chunk_bounces.cause`` set.
BOUNCE_CAUSES = (CAUSE_CONFLICT, CAUSE_CHECKS, CAUSE_MASTER_MOVED)


class BounceCompositionError(Exception):
    """A ``--cause`` or ``--envelope`` :func:`compose_bounce` cannot honor."""


def compose_bounce(
    *,
    chunk_id: str,
    epoch: int,
    recorded_at: datetime,
    cause: str = CAUSE_CONFLICT,
    envelope: str | None = None,
) -> FactRow:
    """One ``chunk_bounces`` row.

    ``envelope`` left unset composes a minimal payload naming the cause; raises
    :class:`BounceCompositionError` for an unknown ``cause`` or an ``envelope``
    that is not a JSON object."""
    if cause not in BOUNCE_CAUSES:
        raise BounceCompositionError(f"unknown cause {cause!r} — one of {BOUNCE_CAUSES}")
    if envelope is None:
        envelope = json.dumps({"cause": cause})
    else:
        try:
            parsed = json.loads(envelope)
        except json.JSONDecodeError as exc:
            raise BounceCompositionError(f"envelope is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise BounceCompositionError("envelope must be a JSON object")
    return FactRow(
        table="chunk_bounces",
        values={
            "chunk_id": chunk_id,
            "epoch": epoch,
            "cause": cause,
            "envelope": envelope,
            "recorded_at": recorded_at,
        },
    )
