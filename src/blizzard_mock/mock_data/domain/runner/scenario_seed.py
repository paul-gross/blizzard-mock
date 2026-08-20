"""Composes the runner-store mirror of ``scenario fleet``'s pinned runner.

Mirrors the hub census's ``waiting_on_human`` and ``needs_human`` chunks into two
dormant shapes: a lease parked on an open ask, and a closed, escalated lease under
an open takeover. No ``running`` chunk is mirrored — that state needs a live worker.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.hub.chunk_seed import NEEDS_HUMAN, WAITING_ON_HUMAN
from blizzard_mock.mock_data.domain.hub.graph_seed import BUILD_NODE_NAME
from blizzard_mock.mock_data.domain.hub.scenario_seed import STATUS_ORDER, BoardCensus
from blizzard_mock.mock_data.domain.runner.ask_seed import compose_ask_park
from blizzard_mock.mock_data.domain.runner.env_binding_seed import compose_env_binding
from blizzard_mock.mock_data.domain.runner.lease_seed import ESCALATED, compose_lease, compose_lease_closure
from blizzard_mock.mock_data.domain.runner.local_pause_seed import compose_local_pause
from blizzard_mock.mock_data.domain.runner.outbound_fact_seed import (
    ESCALATION_RECORDED,
    QUESTION_ASKED,
    compose_outbound_fact,
)
from blizzard_mock.mock_data.domain.runner.takeover_seed import compose_takeover
from blizzard_mock.mock_data.domain.runner.transcript_segment_seed import compose_transcript_segment
from blizzard_mock.mock_data.domain.runner.usage_seed import SPAWN, compose_usage

_RETRIES_MAX = 3
_USAGE_MODEL = "claude-mock-fleet"
_NORMALIZER_VERSION = "mock-1"

#: Local, not in ``domain/ids.py``'s registry — ``_ROUTE_PREFIX``'s precedent.
_SESSION_PREFIX = "sess"


class RunnerFleetCompositionError(Exception):
    """``census`` carries no ``waiting_on_human`` or ``needs_human`` chunk to mirror —
    both dormant shapes need one; a ``--chunks`` too small to reach both raises here
    rather than silently mirroring nothing."""


def minimum_chunks_for_mirror() -> int:
    """The smallest ``--chunks`` carrying both mirrored statuses — a pure function of
    ``STATUS_ORDER``, so ``scenario fleet`` can refuse before writing either store.
    This module owns the fact; both ``compose_runner_fleet`` and the CLI consult it."""
    return max(STATUS_ORDER.index(WAITING_ON_HUMAN), STATUS_ORDER.index(NEEDS_HUMAN)) + 1


@dataclass(frozen=True)
class RunnerFleetScenario:
    """One composed runner-store mirror, split by write order: ``brake`` must land
    before the hub half's ``ready`` chunks, ``rows`` after it."""

    rows: list[FactRow] = field(default_factory=list)
    brake: list[FactRow] = field(default_factory=list)


def _workdir(chunk_id: str) -> str:
    return f"/mock/workdir/{chunk_id}"


def _environment_id(chunk_id: str) -> str:
    return f"env-{chunk_id}"


def compose_runner_fleet(
    *,
    census: BoardCensus,
    graph_id: str,
    runner_id: str,
    clock: Clock,
    rng: random.Random,
) -> RunnerFleetScenario:
    """Mirror ``census``'s ``waiting_on_human`` and ``needs_human`` chunks into the
    runner store under ``runner_id`` and epoch 1 — the epoch every ``scenario board``
    chunk composes under. Raises :class:`RunnerFleetCompositionError` when ``census``
    carries neither chunk (``--chunks`` too small to reach both)."""
    waiting_entry = next((e for e in census.chunk_entries if e.status == WAITING_ON_HUMAN), None)
    needs_human_entry = next((e for e in census.chunk_entries if e.status == NEEDS_HUMAN), None)
    if waiting_entry is None or needs_human_entry is None:
        raise RunnerFleetCompositionError(
            "the hub census carries no waiting_on_human/needs_human chunk to mirror — pass "
            f"--chunks {minimum_chunks_for_mirror()} or more"
        )

    now = clock.now()
    rows: list[FactRow] = []

    # --- waiting_on_human: an active lease, parked on an open ask ---------------
    waiting_lease = compose_lease(
        chunk_id=waiting_entry.chunk_id,
        runner_id=runner_id,
        epoch=1,
        graph_id=graph_id,
        node_id=census.build_node_id,
        node_name=BUILD_NODE_NAME,
        retries_max=_RETRIES_MAX,
        created_at=now,
        clock=clock,
        rng=rng,
    )
    rows.extend(waiting_lease.rows)
    rows.append(
        compose_env_binding(
            chunk_id=waiting_entry.chunk_id,
            environment_id=_environment_id(waiting_entry.chunk_id),
            workdir=_workdir(waiting_entry.chunk_id),
            bound_at=now,
        )
    )
    waiting_question_id = ids.mint(ids.QUESTION_PREFIX, clock, rng)
    rows.extend(
        compose_ask_park(
            lease_id=waiting_lease.lease_id,
            chunk_id=waiting_entry.chunk_id,
            question_id=waiting_question_id,
            question="mock-data: which way should this chunk go? (fleet mirror)",
            session_id=None,
            asked_at=now,
            parked_at=now,
        ).rows
    )
    rows.append(
        compose_usage(
            lease_id=waiting_lease.lease_id,
            chunk_id=waiting_entry.chunk_id,
            node_id=census.build_node_id,
            epoch=1,
            generation=1,
            kind=SPAWN,
            model=_USAGE_MODEL,
            input_tokens=512,
            output_tokens=128,
            cost_usd=0.18,
            recorded_at=now,
        )
    )
    rows.append(
        compose_transcript_segment(
            chunk_id=waiting_entry.chunk_id,
            node_id=census.build_node_id,
            epoch=1,
            generation=1,
            lease_id=waiting_lease.lease_id,
            session_id=ids.mint(_SESSION_PREFIX, clock, rng),
            normalizer_version=_NORMALIZER_VERSION,
            clock=clock,
            rng=rng,
            finalized_at=None,
            stamped_at=now,
        )
    )
    rows.append(
        compose_outbound_fact(
            kind=QUESTION_ASKED,
            chunk_id=waiting_entry.chunk_id,
            lease_id=waiting_lease.lease_id,
            payload={
                "question_id": waiting_question_id,
                "chunk_id": waiting_entry.chunk_id,
                "epoch": 1,
                "question": "mock-data: which way should this chunk go? (fleet mirror)",
            },
            created_at=now,
            acked_at=now,
        )
    )

    # --- needs_human: a closed, escalated lease under an open takeover ------
    needs_human_session_id = ids.mint(_SESSION_PREFIX, clock, rng)
    needs_human_lease = compose_lease(
        chunk_id=needs_human_entry.chunk_id,
        runner_id=runner_id,
        epoch=1,
        graph_id=graph_id,
        node_id=census.build_node_id,
        node_name=BUILD_NODE_NAME,
        retries_max=_RETRIES_MAX,
        created_at=now,
        clock=clock,
        rng=rng,
        session_id=needs_human_session_id,
    )
    rows.extend(needs_human_lease.rows)
    rows.append(
        compose_lease_closure(
            lease_id=needs_human_lease.lease_id,
            chunk_id=needs_human_entry.chunk_id,
            node_id=census.build_node_id,
            reason=ESCALATED,
            closed_at=now,
        )
    )
    needs_human_workdir = _workdir(needs_human_entry.chunk_id)
    rows.append(
        compose_env_binding(
            chunk_id=needs_human_entry.chunk_id,
            environment_id=_environment_id(needs_human_entry.chunk_id),
            workdir=needs_human_workdir,
            bound_at=now,
        )
    )
    rows.append(
        compose_takeover(
            chunk_id=needs_human_entry.chunk_id,
            lease_id=needs_human_lease.lease_id,
            session_id=needs_human_session_id,
            workdir=needs_human_workdir,
            # No live worker was force-killed to open this takeover — the reference
            # lease is already closed (``TakeoverService.open``'s ``live=False`` arm).
            fence_epoch=None,
            opened_at=now,
            clock=clock,
            rng=rng,
        )
    )
    rows.append(
        compose_usage(
            lease_id=needs_human_lease.lease_id,
            chunk_id=needs_human_entry.chunk_id,
            node_id=census.build_node_id,
            epoch=1,
            generation=1,
            kind=SPAWN,
            model=_USAGE_MODEL,
            input_tokens=768,
            output_tokens=201,
            cost_usd=0.27,
            recorded_at=now,
        )
    )
    rows.append(
        compose_transcript_segment(
            chunk_id=needs_human_entry.chunk_id,
            node_id=census.build_node_id,
            epoch=1,
            generation=1,
            lease_id=needs_human_lease.lease_id,
            session_id=needs_human_session_id,
            normalizer_version=_NORMALIZER_VERSION,
            clock=clock,
            rng=rng,
            finalized_at=now,
            stamped_at=now,
        )
    )
    rows.append(
        compose_outbound_fact(
            kind=ESCALATION_RECORDED,
            chunk_id=needs_human_entry.chunk_id,
            lease_id=needs_human_lease.lease_id,
            payload={"chunk_id": needs_human_entry.chunk_id, "epoch": 1, "runner_id": runner_id},
            created_at=now,
            acked_at=now,
        )
    )

    return RunnerFleetScenario(rows=rows, brake=[compose_local_pause(runner_id=runner_id, set_at=now)])
