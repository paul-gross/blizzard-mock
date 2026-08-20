"""Composes one runner-store ``usage_facts`` row.

Keyed by ``lease_id`` + ``generation`` rather than ``runner_id`` — the runner schema's
own column set, distinct from the hub's (``domain/hub/usage_seed.py``). ``cost_usd``
is genuinely nullable; ``compose_usage`` never substitutes ``0.0``.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

SPAWN = "spawn"
RESUME = "resume"
JUDGE = "judge"
NUDGE = "nudge"

#: Every usage kind ``usage_facts.kind`` accepts, mirrored independently (no
#: ``blizzard`` import) — the same vocabulary the hub side mirrors.
KINDS = (SPAWN, RESUME, JUDGE, NUDGE)


class UsageCompositionError(Exception):
    """A ``--kind`` ``compose_usage`` cannot honor."""


def compose_usage(
    *,
    lease_id: str,
    chunk_id: str,
    node_id: str,
    epoch: int,
    generation: int,
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
            "lease_id": lease_id,
            "chunk_id": chunk_id,
            "node_id": node_id,
            "epoch": epoch,
            "generation": generation,
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
