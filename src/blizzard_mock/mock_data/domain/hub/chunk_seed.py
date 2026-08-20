"""Composes one chunk's ``FactRow`` set for a requested derived status (``bzh:facts-not-status``).

:func:`compose_chunk` returns the exact fact rows the hub's own
``derive_chunk_status`` reads to arrive at that status, first-match-wins —
never a status column. A pure function of its inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.hub.graph_seed import BUILD_NODE_NAME, DELIVER_NODE_NAME, GraphContext
from blizzard_mock.mock_data.domain.hub.lease_seed import compose_lease_row

STOPPED = "stopped"
DONE = "done"
NEEDS_HUMAN = "needs_human"
WAITING_ON_HUMAN = "waiting_on_human"
PAUSED = "paused"
DELIVERING = "delivering"
RUNNING = "running"
NOT_READY = "not_ready"
READY = "ready"

#: Every derivable status ``compose_chunk`` accepts, in ``derive_chunk_status``'s own
#: precedence order (highest first) — the CLI's ``click.Choice`` is built from this.
STATUSES = (STOPPED, DONE, NEEDS_HUMAN, WAITING_ON_HUMAN, PAUSED, DELIVERING, RUNNING, NOT_READY, READY)

#: The statuses ``scenario fleet`` mirrors — the only ones ``mirrored=True`` is safe
#: for, since a live route outranks every status below it.
MIRRORED_STATUSES = frozenset({WAITING_ON_HUMAN, NEEDS_HUMAN})

# A local id-prefix, deliberately not in ``domain/ids.py``'s shared registry,
# mirroring that the real one is its own module constant too.
_ROUTE_PREFIX = "route"

_RESERVED_TERMINAL = "done"  # mirrors the hub's reserved terminal, independently

_DEFAULT_WORKSPACE_ID = "workspace-seed"


class ChunkCompositionError(Exception):
    """A ``--status``/``--node``/``--graph`` combination ``compose_chunk`` cannot honor."""


@dataclass(frozen=True)
class ChunkSeed:
    """One composed chunk: its minted id and the exact ``FactRow``\\ s to write."""

    chunk_id: str
    rows: list[FactRow] = field(default_factory=list)


def compose_chunk(
    *,
    status: str,
    graph: GraphContext,
    clock: Clock,
    rng: Random,
    chunk_id: str | None = None,
    node_name: str | None = None,
    work_refs: Sequence[tuple[str, str]] = (),
    runner_id: str = "runner-seed",
    epoch: int = 1,
    workspace_id: str = _DEFAULT_WORKSPACE_ID,
    mirrored: bool = False,
) -> ChunkSeed:
    """Compose one chunk minted onto ``graph``, landing at ``status``. Raises
    :class:`ChunkCompositionError` for an unknown status, an unresolvable node name,
    or an unreachable ``--node``/``--status`` pairing. ``mirrored=True`` adds a live
    ``route_created`` — valid only for :data:`MIRRORED_STATUSES`."""
    if status not in STATUSES:
        raise ChunkCompositionError(f"unknown status {status!r} — one of {STATUSES}")

    minted_chunk_id = chunk_id or ids.mint(ids.CHUNK_PREFIX, clock, rng)
    now = clock.now()

    def at(offset_seconds: int) -> datetime:
        return now + timedelta(seconds=offset_seconds)

    rows: list[FactRow] = [
        FactRow(
            table="chunks",
            values={
                "chunk_id": minted_chunk_id,
                "graph_id": graph.graph_id,
                "minted_at": now,
                # Retained-and-unread since blizzard issue #144 (schema.py's own note) —
                # a placeholder is all a fresh row needs; nothing reads it as current.
                "model": "mock-data-seed",
                "default_model": None,
                "default_effort": None,
                "intended_migration": None,
            },
        )
    ]
    rows.extend(
        FactRow(table="chunk_work_refs", values={"chunk_id": minted_chunk_id, "source": source, "ref": ref})
        for source, ref in work_refs
    )

    def promoted(offset_seconds: int = 1) -> FactRow:
        return FactRow(table="chunk_promoted", values={"chunk_id": minted_chunk_id, "promoted_at": at(offset_seconds)})

    def transition(
        *, offset_seconds: int, to_node_id: str, from_node_id: str | None = None, choice_name: str | None = None
    ) -> FactRow:
        return FactRow(
            table="transitions",
            values={
                "transition_id": ids.mint(ids.TRANSITION_PREFIX, clock, rng),
                "chunk_id": minted_chunk_id,
                "graph_id": graph.graph_id,
                "from_node_id": from_node_id,
                "to_node_id": to_node_id,
                "choice_name": choice_name,
                "decision_id": None,
                "epoch": epoch,
                "runner_id": runner_id,
                "recorded_at": at(offset_seconds),
            },
        )

    def lease(offset_seconds: int) -> FactRow:
        return compose_lease_row(
            chunk_id=minted_chunk_id, epoch=epoch, runner_id=runner_id, minted_at=at(offset_seconds)
        )

    def route_created(offset_seconds: int) -> FactRow:
        return FactRow(
            table="route_created",
            values={
                "route_id": ids.mint(_ROUTE_PREFIX, clock, rng),
                "chunk_id": minted_chunk_id,
                "runner_id": runner_id,
                "workspace_id": workspace_id,
                "created_at": at(offset_seconds),
                "seq": 1,
            },
        )

    def refuse_node() -> None:
        if node_name is not None:
            raise ChunkCompositionError(f"--status {status} mints no transition — --node has nothing to land on")

    def finish(offset_seconds: int = 5) -> ChunkSeed:
        """Land the mirroring ``route_created`` this status's own branch didn't, then
        return. A no-op for ``running``/``delivering``, which already minted one."""
        if mirrored and not any(row.table == "route_created" for row in rows):
            rows.append(route_created(offset_seconds))
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

    if status == STOPPED:
        rows.append(promoted())
        if node_name is not None:
            rows.append(transition(offset_seconds=2, to_node_id=graph.node(node_name).node_id))
        rows.append(
            FactRow(
                table="chunk_stopped",
                values={"chunk_id": minted_chunk_id, "stopped_at": at(3), "stopped_by": "mock-data"},
            )
        )
        return finish()

    if status == DONE:
        rows.append(promoted())
        rows.append(lease(2))
        # The graph's own edges: build --approved--> deliver --landed--> the
        # reserved terminal — one epoch, two node-steps.
        build_node = graph.node(BUILD_NODE_NAME)
        deliver_node = graph.node(node_name or DELIVER_NODE_NAME)
        # A terminal hop already leaving ``build`` has no predecessor: no graph carries a self-edge.
        if deliver_node.node_id != build_node.node_id:
            rows.append(
                transition(
                    offset_seconds=3,
                    from_node_id=build_node.node_id,
                    to_node_id=deliver_node.node_id,
                    choice_name="approved",
                )
            )
        rows.append(
            transition(
                offset_seconds=4,
                from_node_id=deliver_node.node_id,
                to_node_id=_RESERVED_TERMINAL,
                choice_name="landed",
            )
        )
        return finish()

    if status == NEEDS_HUMAN:
        rows.append(promoted())
        node = graph.node(node_name or BUILD_NODE_NAME)
        rows.append(transition(offset_seconds=2, to_node_id=node.node_id))
        rows.append(
            FactRow(
                table="escalations",
                values={
                    "chunk_id": minted_chunk_id,
                    "epoch": epoch,
                    "takeover_command": f"cd <workdir> && <resume {minted_chunk_id}>",
                    "wrapped_takeover_command": f"blizzard runner takeover {minted_chunk_id} --dir <runner-dir>",
                    "decision_id": None,
                    "recorded_at": at(3),
                },
            )
        )
        return finish()

    if status == WAITING_ON_HUMAN:
        rows.append(promoted())
        node = graph.node(node_name or BUILD_NODE_NAME)
        rows.append(transition(offset_seconds=2, to_node_id=node.node_id))
        rows.append(
            FactRow(
                table="questions",
                values={
                    "question_id": ids.mint(ids.QUESTION_PREFIX, clock, rng),
                    "chunk_id": minted_chunk_id,
                    "node_id": node.node_id,
                    "session_id": None,
                    "runner_id": runner_id,
                    "epoch": epoch,
                    "question": "mock-data: which way should this chunk go?",
                    "options": "[]",
                    "asked_at": at(3),
                },
            )
        )
        return finish()

    if status == PAUSED:
        rows.append(promoted())
        node = graph.node(node_name or BUILD_NODE_NAME)
        rows.append(transition(offset_seconds=2, to_node_id=node.node_id))
        rows.append(
            FactRow(
                table="chunk_pause_facts",
                values={"chunk_id": minted_chunk_id, "paused": True, "set_at": at(3), "set_by": "mock-data"},
            )
        )
        return finish()

    if status == DELIVERING:
        rows.append(promoted())
        rows.append(lease(2))
        resolved_name = node_name or DELIVER_NODE_NAME
        node = graph.node(resolved_name)
        if node.executor != "hub":
            raise ChunkCompositionError(
                f"--status delivering requires a hub-executor node; {resolved_name!r} is {node.executor!r}"
            )
        rows.append(route_created(3))
        rows.append(transition(offset_seconds=4, to_node_id=node.node_id))
        return finish()

    if status == RUNNING:
        rows.append(promoted())
        rows.append(lease(2))
        resolved_name = node_name or BUILD_NODE_NAME
        node = graph.node(resolved_name)
        if node.executor == "hub":
            raise ChunkCompositionError(
                f"--status running requires a non-hub node (a hub-node transition derives delivering instead); "
                f"{resolved_name!r} is hub-executed"
            )
        rows.append(route_created(3))
        rows.append(transition(offset_seconds=4, to_node_id=node.node_id))
        return finish()

    if status == NOT_READY:
        refuse_node()
        return finish()

    assert status == READY
    refuse_node()
    rows.append(promoted())
    return finish()
