"""Composes one runner-store lease — the ``leases`` row plus its ``lease_context``
sibling, always together: every daemon lease read inner-joins ``lease_context``, so
a bare ``leases`` row passes the drift guard and renders nowhere.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow


@dataclass(frozen=True)
class RunnerLeaseSeed:
    """One composed runner lease: its minted id and the two ``FactRow``\\ s to write."""

    lease_id: str
    rows: list[FactRow] = field(default_factory=list)


def compose_lease(
    *,
    chunk_id: str,
    runner_id: str,
    epoch: int,
    graph_id: str,
    node_id: str,
    node_name: str,
    retries_max: int,
    created_at: datetime,
    clock: Clock,
    rng: random.Random,
    session_id: str | None = None,
) -> RunnerLeaseSeed:
    """One fresh lease mint: ``leases`` plus its ``lease_context``, the shape a real
    mint writes before the worker exists. ``pid``/``process_start_time`` land NULL,
    and ``session_id`` too unless the caller supplies one."""
    lease_id = ids.mint(ids.LEASE_PREFIX, clock, rng)
    rows = [
        FactRow(
            table="leases",
            values={
                "lease_id": lease_id,
                "chunk_id": chunk_id,
                "epoch": epoch,
                "runner_id": runner_id,
                "pid": None,
                "process_start_time": None,
                "session_id": session_id,
                "created_at": created_at,
            },
        ),
        FactRow(
            table="lease_context",
            values={
                "lease_id": lease_id,
                "chunk_id": chunk_id,
                "graph_id": graph_id,
                "node_id": node_id,
                "node_name": node_name,
                "retries_max": retries_max,
                "session_name": None,
                "resolved_model": None,
                "resolved_effort": None,
                "resolved_compaction_window": None,
                "recorded_at": created_at,
            },
        ),
    ]
    return RunnerLeaseSeed(lease_id=lease_id, rows=rows)


#: ``lease_closures.reason`` values the runner store's own schema names
#: (``blizzard/src/blizzard/runner/store/schema.py``).
TRANSITIONED = "transitioned"
REAPED = "reaped"
FAILED = "failed"
ESCALATED = "escalated"


def compose_lease_closure(*, lease_id: str, chunk_id: str, node_id: str, reason: str, closed_at: datetime) -> FactRow:
    """One ``lease_closures`` row — closes ``lease_id``, dropping it out of
    ``OPEN_LEASE`` (``sqlalchemy_store.py``). ``reason="escalated"`` is what
    ``open_escalations`` selects on, alongside an unresolved
    ``escalation_closures`` row (composed nowhere here, so the escalation stays
    open) — the ``needs_human`` mirror ``domain/runner/scenario_seed.py`` composes."""
    return FactRow(
        table="lease_closures",
        values={
            "lease_id": lease_id,
            "chunk_id": chunk_id,
            "node_id": node_id,
            "reason": reason,
            "closed_at": closed_at,
        },
    )
