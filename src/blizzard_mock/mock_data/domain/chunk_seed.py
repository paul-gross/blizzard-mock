"""Composes one chunk's ``FactRow`` set for a requested derived status (``bzh:facts-not-status``).

:func:`compose_chunk` is ``create chunk``'s pure heart: given a resolved
:class:`~blizzard_mock.mock_data.domain.graph_seed.GraphContext` (minted fresh, or
hydrated from an existing store row — ``graph_seed.py``) and one of the nine derived
statuses below, it returns the **exact** fact rows the hub's own
``derive_chunk_status`` (``blizzard/hub/domain/work.py``) reads to arrive at that
status, first-match-wins per its precedence — never a status column. A pure function
of its inputs (``bzh:domain-takes-objects``), unit-tested with zero store.

The nine status constants mirror ``blizzard.hub.domain.work.ChunkStatus``'s member
values — independently kept in step, no ``blizzard`` import (the mock-data
contract's first property), the same precedent ``domain/ids.py``'s prefix registry
and this module's own graph counterpart set.

Per-status composition, precedence-ordered top to bottom (the same order
``derive_chunk_status`` checks, so each entry names exactly the fact(s) that outrank
every status below it):

- ``stopped`` — a ``chunk_stopped`` row. Checked first, so a transition to *any* node
  (including a hub node) is safe here and never flips the derivation.
- ``done`` — a transition whose ``to_node_id`` is the reserved terminal ``"done"``.
- ``needs_human`` — an ``escalations`` row with no later lease/requeue (trivially open
  when no lease/requeue exists at all, which is what this composes).
- ``waiting_on_human`` — an open (unanswered) ``questions`` row.
- ``paused`` — a ``chunk_pause_facts`` row reading ``paused=True``.
- ``delivering`` — the newest transition's target is a **hub-executor** node; refuses
  an explicit ``--node`` that resolves to a non-hub node (the composed status would
  not be reachable).
- ``running`` — a live ``route_created`` (no ``route_released``) with the newest
  transition's target a **non-hub** node; refuses an explicit ``--node`` that
  resolves to a hub node (it would instead derive ``delivering``).
- ``not_ready`` — no ``chunk_promoted`` row. Mints no transition (a chunk that has
  never moved sits at the graph's entry node); ``--node`` is refused; there is
  nothing for it to land on.
- ``ready`` — a ``chunk_promoted`` row and nothing that outranks it. Same
  no-transition/no-``--node`` shape as ``not_ready``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.graph_seed import BUILD_NODE_NAME, DELIVER_NODE_NAME, GraphContext
from blizzard_mock.mock_data.domain.lease_seed import compose_lease_row

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

# A local id-prefix, like blizzard's own ``chunk_store._ROUTE_PREFIX`` — deliberately
# not in ``domain/ids.py``'s shared registry, mirroring that the real one isn't in
# ``blizzard.foundation.ids`` either (it is ``ChunkStore``'s own module constant).
_ROUTE_PREFIX = "route"

_RESERVED_TERMINAL = "done"  # blizzard.hub.domain.graph.RESERVED_TERMINAL, independently mirrored

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
) -> ChunkSeed:
    """Compose one chunk minted onto ``graph``, landing at ``status``.

    ``node_name`` names the graph node the chunk's composed transition lands on
    (``--node``); status-dependent defaults apply where a transition is minted at
    all (see the module docstring). ``workspace_id`` is the ``route_created`` fact's
    own attribution (``running``/``delivering`` only, where a route is minted at
    all) — callers that also register the attributed ``runner_id`` under a specific
    workspace (e.g. ``scenario_seed.py``) should pass the same id here, so a
    chunk's live route doesn't claim a workspace its own runner isn't registered
    under. Raises :class:`ChunkCompositionError` for an unknown status, an
    unresolvable node name (via
    :meth:`~blizzard_mock.mock_data.domain.graph_seed.GraphContext.node`), or a
    ``--node``/``--status`` pairing that cannot reach the requested status.
    """
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
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

    if status == DONE:
        rows.append(promoted())
        rows.append(lease(2))
        from_node = graph.node(node_name or DELIVER_NODE_NAME)
        rows.append(
            transition(
                offset_seconds=3,
                to_node_id=_RESERVED_TERMINAL,
                from_node_id=from_node.node_id,
                choice_name="landed",
            )
        )
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

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
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

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
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

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
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

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
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

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
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

    if status == NOT_READY:
        refuse_node()
        return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)

    assert status == READY
    refuse_node()
    rows.append(promoted())
    return ChunkSeed(chunk_id=minted_chunk_id, rows=rows)
