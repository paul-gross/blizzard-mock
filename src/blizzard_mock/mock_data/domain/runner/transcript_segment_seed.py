"""Composes one runner-store ``transcript_segments`` row — runner-only, no hub
counterpart exists.

Mints its own ``segment_id`` (``bzh:domain-core``). ``finalized_at`` NULL means the
segment is still open.
"""

from __future__ import annotations

import random
from datetime import datetime

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow


def compose_transcript_segment(
    *,
    chunk_id: str,
    node_id: str,
    epoch: int,
    generation: int,
    lease_id: str,
    session_id: str,
    normalizer_version: str,
    clock: Clock,
    rng: random.Random,
    cursor: str | None = None,
    shipped_bytes: int = 0,
    shipped_turns: int = 0,
    harness_version: str | None = None,
    finalized_at: datetime | None = None,
    stamped_at: datetime,
) -> FactRow:
    """One ``transcript_segments`` row, minting its own ``seg_<ulid>`` id."""
    return FactRow(
        table="transcript_segments",
        values={
            "segment_id": ids.mint(ids.SEGMENT_PREFIX, clock, rng),
            "chunk_id": chunk_id,
            "node_id": node_id,
            "epoch": epoch,
            "generation": generation,
            "lease_id": lease_id,
            "session_id": session_id,
            "cursor": cursor,
            "shipped_bytes": shipped_bytes,
            "shipped_turns": shipped_turns,
            "normalizer_version": normalizer_version,
            "harness_version": harness_version,
            "truncated_reason": None,
            "truncated_reason_severity": None,
            "shipping_stopped_reason": None,
            "sidechain_warned_agents": None,
            "agent_tool_use_ids": None,
            "truncated_reasons_warned": None,
            "supersedes": None,
            "finalized_at": finalized_at,
            "stamped_at": stamped_at,
        },
    )
