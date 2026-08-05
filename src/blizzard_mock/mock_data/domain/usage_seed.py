"""Composes one ``usage_facts`` row — one harness invocation's usage/cost
telemetry (issue #59).

``cost_usd`` is genuinely nullable; ``compose_usage`` never substitutes ``0.0``
(pinned by tests/test_mock_data_usage_seed.py).
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

SPAWN = "spawn"
RESUME = "resume"
JUDGE = "judge"
NUDGE = "nudge"

#: Every usage kind ``usage_facts.kind`` accepts, mirrored independently (no
#: ``blizzard`` import).
KINDS = (SPAWN, RESUME, JUDGE, NUDGE)


class UsageCompositionError(Exception):
    """A ``--kind`` ``compose_usage`` cannot honor."""


def compose_usage(
    *,
    chunk_id: str,
    node_id: str,
    epoch: int,
    runner_id: str,
    kind: str,
    model: str,
    input_tokens: int,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
    cost_usd: float | None,
    recorded_at: datetime,
) -> FactRow:
    """One ``usage_facts`` row. Raises :class:`UsageCompositionError` for an unknown ``kind``."""
    if kind not in KINDS:
        raise UsageCompositionError(f"unknown usage kind {kind!r} — one of {KINDS}")
    return FactRow(
        table="usage_facts",
        values={
            "chunk_id": chunk_id,
            "node_id": node_id,
            "epoch": epoch,
            "runner_id": runner_id,
            "kind": kind,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_create_tokens": cache_create_tokens,
            "cost_usd": cost_usd,
            "recorded_at": recorded_at,
        },
    )
