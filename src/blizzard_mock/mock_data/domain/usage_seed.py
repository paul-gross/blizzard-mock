"""Composes one ``usage_facts`` row — one harness invocation's usage/cost telemetry
(``blizzard/hub/store/schema.py``'s ``usage_facts``, issue #59, ``bzh:facts-not-status``).

``cost_usd`` is genuinely nullable — ``None`` is the harness-envelope-less
transcript-token fallback (e.g. after a reaped crash) the real runner writes, which the
hub's cost derivation reads as a lower bound and flags ``cost_partial`` for
(``blizzard.runner.loop.steps._park_on_cost_cap``'s docstring). ``compose_usage`` never
substitutes ``0.0`` for an absent cost — the caller's ``cost_usd=None`` lands a genuine
SQL NULL, never a fabricated number.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow

SPAWN = "spawn"
RESUME = "resume"
JUDGE = "judge"
NUDGE = "nudge"

#: Every usage kind ``usage_facts.kind`` accepts (``blizzard.runner.harness.adapter``'s
#: own usage-kind vocabulary, independently mirrored — no ``blizzard`` import).
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
