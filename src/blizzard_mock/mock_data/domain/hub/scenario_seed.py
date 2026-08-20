"""Composes the ``scenario board`` verb's whole ``FactRow`` set (``bzh:domain-core``).

Mints one graph, spreads ``--chunks`` chunks across the nine derived statuses
(:data:`STATUS_ORDER`, deterministic under ``--seed``), a cost spread, an
artifact spread, a ceiling-paused runner, a runner fleet, and a mixed event log.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.hub.artifact_seed import ASSET, GIT_COMMIT, compose_artifact
from blizzard_mock.mock_data.domain.hub.chunk_seed import (
    DELIVERING,
    DONE,
    MIRRORED_STATUSES,
    NEEDS_HUMAN,
    NOT_READY,
    PAUSED,
    READY,
    RUNNING,
    STOPPED,
    WAITING_ON_HUMAN,
    compose_chunk,
)
from blizzard_mock.mock_data.domain.hub.event_seed import CRITICAL, INFO, WARNING, compose_event
from blizzard_mock.mock_data.domain.hub.graph_seed import (
    BUILD_NODE_NAME,
    DEFAULT_GRAPH_NAME,
    DELIVER_NODE_NAME,
    GraphContext,
    NodeRef,
    compose_graph,
)
from blizzard_mock.mock_data.domain.hub.question_seed import compose_question
from blizzard_mock.mock_data.domain.hub.runner_pause_seed import compose_runner_pause
from blizzard_mock.mock_data.domain.hub.usage_seed import RESUME, SPAWN, compose_usage

#: The nine statuses, board-interesting-first — the priority list
#: :func:`compose_board_scenario`'s deterministic assignment cycles through.
STATUS_ORDER: tuple[str, ...] = (
    READY,
    RUNNING,
    NEEDS_HUMAN,
    WAITING_ON_HUMAN,
    DONE,
    PAUSED,
    DELIVERING,
    STOPPED,
    NOT_READY,
)

#: ``scenario board``'s own ``--chunks`` default — "something reasonable" that
#: covers every one of the five statuses ``--chunks < 9`` must, plus one more.
DEFAULT_CHUNKS = 6

_USAGE_MODEL = "claude-mock-scenario"
_CEILING_PAUSE_REASON = "spend ceiling $50.00 reached over the trailing 24h (spend $52.30)"
_DEFAULT_EVENT_RUNNER_ID = "mock-data"

#: The workspace a pinned runner's chunks route under when this composer also mints
#: its registration; ``register_runner=False`` leaves the standing one authoritative.
_PINNED_WORKSPACE_ID = "workspace-fleet"

#: Deliberately long strings for ``--stress``'s overflow-UI extremes — long
#: enough to blow past any reasonable column/badge width.
_STRESS_LONG_RUNNER_ID = "runner-" + (
    "-".join(["extremely", "long", "runner", "identity", "for", "narrow", "viewport", "checks"]) * 3
)
_STRESS_LONG_NODE_NAME = "custom-" + (
    "-".join(["a", "very", "long", "graph", "node", "name", "for", "overflow", "checks"]) * 3
)
_STRESS_LONG_ARTIFACT_NAME = "artifact-" + (
    "-".join(["a", "very", "long", "produced", "artifact", "name", "for", "overflow", "checks"]) * 3
)


class ScenarioCompositionError(Exception):
    """A ``--chunks``/``--seed`` combination :func:`compose_board_scenario` cannot honor."""


@dataclass(frozen=True)
class ChunkCensusEntry:
    """One seeded chunk's id and the status it was composed to derive."""

    chunk_id: str
    status: str


@dataclass(frozen=True)
class BoardCensus:
    """What a seeded scenario board holds — the ``scenario board`` verb's printed summary."""

    chunk_entries: list[ChunkCensusEntry]
    status_counts: Mapping[str, int]
    #: The distinct runner ids this board's chunks are attributed to — one entry per
    #: runner, not one per chunk.
    runner_ids: list[str]
    usage_fact_count: int
    cost_partial_count: int
    artifact_count: int
    ceiling_paused_runner_id: str
    stress: bool
    #: The minted ``build`` node's id — the runner-store mirror lands its
    #: lease/binding here, so it needs no store re-read.
    build_node_id: str

    @property
    def chunk_count(self) -> int:
        return len(self.chunk_entries)

    @property
    def runner_count(self) -> int:
        return len(self.runner_ids)


@dataclass(frozen=True)
class BoardScenario:
    """One composed scenario board: its minted graph id, every ``FactRow`` to
    write, and the :class:`BoardCensus` a human (or a test) reads to confirm
    what landed."""

    graph_id: str
    rows: list[FactRow] = field(default_factory=list)
    census: BoardCensus | None = None


def _runner_registration_row(runner_id: str, workspace_id: str, now: datetime) -> FactRow:
    """The same ``runner_registrations`` row shape ``create runner`` builds inline."""
    return FactRow(
        table="runner_registrations",
        values={"runner_id": runner_id, "workspace_id": workspace_id, "registered_at": now, "last_seen_at": now},
    )


def _status_node_id(status: str, graph: GraphContext) -> str:
    """The node a status's own composed transition (if any) lands on — mirrors
    :func:`compose_chunk`'s per-status node defaults."""
    if status in (DELIVERING, DONE):
        return graph.node(DELIVER_NODE_NAME).node_id
    return graph.node(BUILD_NODE_NAME).node_id


def _extend_graph_with_node(graph: GraphContext, name: str, clock: Clock, rng: Random) -> tuple[GraphContext, FactRow]:
    """Mint one extra ``graph_nodes`` row onto ``graph``, under a caller-chosen
    ``name``. Deliberately mints no ``graph_choices``/``graph_edges`` for it — a
    structural dead end, harmless because nothing here validates reachability.
    """
    node_id = ids.mint(ids.NODE_PREFIX, clock, rng)
    row = FactRow(
        table="graph_nodes",
        values={
            "node_id": node_id,
            "graph_id": graph.graph_id,
            "name": name,
            "executor": "runner",
            "session": "fresh",
            "judged_by": "worker",
        },
    )
    extended_nodes = dict(graph.nodes)
    extended_nodes[name] = NodeRef(node_id=node_id, executor="runner")
    extended = GraphContext(graph_id=graph.graph_id, entry_node_id=graph.entry_node_id, nodes=extended_nodes)
    return extended, row


@dataclass(frozen=True)
class _StressExtras:
    rows: list[FactRow]
    runner_id: str
    chunk_entries: list[ChunkCensusEntry]


def _compose_stress_extras(*, graph: GraphContext, clock: Clock, rng: Random) -> _StressExtras:
    """The five ``--stress`` extremes: a long-identity runner, a chunk landed on a
    long custom node name carrying a deliberately long artifact name, and one
    ``waiting_on_human`` chunk carrying two extra independent question trails."""
    rows: list[FactRow] = []
    now = clock.now()
    long_runner_id = _STRESS_LONG_RUNNER_ID
    rows.append(_runner_registration_row(long_runner_id, "workspace-stress", now))

    waiting_seed = compose_chunk(status=WAITING_ON_HUMAN, graph=graph, clock=clock, rng=rng, runner_id=long_runner_id)
    rows.extend(waiting_seed.rows)
    for question_text in (
        "mock-data: a second pending question (stress)",
        "mock-data: a third pending question (stress)",
    ):
        extra_question = compose_question(
            chunk_id=waiting_seed.chunk_id, clock=clock, rng=rng, question=question_text, runner_id=long_runner_id
        )
        rows.extend(extra_question.rows)

    extended_graph, node_row = _extend_graph_with_node(graph, _STRESS_LONG_NODE_NAME, clock, rng)
    rows.append(node_row)
    long_node_seed = compose_chunk(
        status=RUNNING,
        graph=extended_graph,
        clock=clock,
        rng=rng,
        node_name=_STRESS_LONG_NODE_NAME,
        runner_id=long_runner_id,
        workspace_id="workspace-stress",
    )
    rows.extend(long_node_seed.rows)
    rows.append(
        compose_artifact(
            chunk_id=long_node_seed.chunk_id,
            node_id=extended_graph.node(_STRESS_LONG_NODE_NAME).node_id,
            node_name=_STRESS_LONG_NODE_NAME,
            epoch=1,
            name=_STRESS_LONG_ARTIFACT_NAME,
            kind=ASSET,
            clock=clock,
            rng=rng,
            content_size=32,
        )
    )

    chunk_entries = [
        ChunkCensusEntry(chunk_id=waiting_seed.chunk_id, status=WAITING_ON_HUMAN),
        ChunkCensusEntry(chunk_id=long_node_seed.chunk_id, status=RUNNING),
    ]
    return _StressExtras(rows=rows, runner_id=long_runner_id, chunk_entries=chunk_entries)


def compose_board_scenario(
    *,
    chunks: int = DEFAULT_CHUNKS,
    clock: Clock,
    rng: Random,
    stress: bool = False,
    graph_name: str | None = None,
    runner_id: str | None = None,
    register_runner: bool = True,
) -> BoardScenario:
    """Compose one whole scenario board: a graph, ``chunks`` chunks spread across the
    nine statuses, a cost and artifact spread, a ceiling-paused runner, a runner per
    chunk, a mixed event log, and the ``stress=True`` extremes. ``runner_id`` pins
    every chunk to one runner; ``register_runner=False`` skips its registration row
    when the composition root already found a live one. See ``mock_data/README.md``."""
    if chunks < 1:
        raise ScenarioCompositionError(f"--chunks must be at least 1, got {chunks}")

    pinned = runner_id is not None
    rows: list[FactRow] = []
    minted_graph = compose_graph(graph_name or DEFAULT_GRAPH_NAME, clock, rng)
    rows.extend(minted_graph.rows)
    graph = minted_graph.context
    now = clock.now()
    build_node_id = graph.node(BUILD_NODE_NAME).node_id

    chunk_entries: list[ChunkCensusEntry] = []
    #: Per-chunk, index-aligned with ``chunk_entries``; distinct from the census's
    #: ``runner_ids``, which holds one entry per distinct runner.
    chunk_runner_ids: list[str] = []
    runner_workspaces: dict[str, str] = {}
    for i in range(chunks):
        status = STATUS_ORDER[i % len(STATUS_ORDER)]
        chunk_runner_id = runner_id if pinned else f"runner-{i:02d}"
        chunk_workspace_id = _PINNED_WORKSPACE_ID if pinned else f"workspace-{i:02d}"
        seeded_chunk = compose_chunk(
            status=status,
            graph=graph,
            clock=clock,
            rng=rng,
            runner_id=chunk_runner_id,
            workspace_id=chunk_workspace_id,
            mirrored=pinned and status in MIRRORED_STATUSES,
        )
        rows.extend(seeded_chunk.rows)
        chunk_entries.append(ChunkCensusEntry(chunk_id=seeded_chunk.chunk_id, status=status))
        chunk_runner_ids.append(chunk_runner_id)
        runner_workspaces.setdefault(chunk_runner_id, chunk_workspace_id)

    for rid, workspace_id in runner_workspaces.items():
        if not register_runner and rid == runner_id:
            continue  # the composition root already found a live registration for it
        rows.append(_runner_registration_row(rid, workspace_id, now))

    usage_fact_count = 0
    cost_partial_count = 0
    for i, entry in enumerate(chunk_entries):
        if i % 2 != 0:
            continue
        cost_usd = None if i == 0 else round(0.42 + i * 0.83, 2)
        rows.append(
            compose_usage(
                chunk_id=entry.chunk_id,
                node_id=_status_node_id(entry.status, graph),
                epoch=1,
                runner_id=chunk_runner_ids[i],
                kind=SPAWN if i == 0 else RESUME,
                model=_USAGE_MODEL,
                input_tokens=400 + i * 137,
                output_tokens=90 + i * 31,
                cost_usd=cost_usd,
                recorded_at=now,
            )
        )
        usage_fact_count += 1
        if cost_usd is None:
            cost_partial_count += 1

    # A representative spread: the ``ready`` chunk, an open ``waiting_on_human``
    # chunk, and the ``done`` chunk's own two node-steps each carry an artifact.
    ready_entry = chunk_entries[0]
    rows.append(
        compose_artifact(
            chunk_id=ready_entry.chunk_id,
            node_id=graph.node(BUILD_NODE_NAME).node_id,
            node_name=BUILD_NODE_NAME,
            epoch=1,
            name="scenario-notes",
            kind=ASSET,
            clock=clock,
            rng=rng,
            content="mock-data scenario board: notes captured before promotion.",
        )
    )
    waiting_entry = next((entry for entry in chunk_entries if entry.status == WAITING_ON_HUMAN), None)
    if waiting_entry is not None:
        rows.append(
            compose_artifact(
                chunk_id=waiting_entry.chunk_id,
                node_id=graph.node(BUILD_NODE_NAME).node_id,
                node_name=BUILD_NODE_NAME,
                epoch=1,
                name="feature-branch",
                kind=GIT_COMMIT,
                clock=clock,
                rng=rng,
                repo="acme/widget",
                branch="feature/scenario-question",
                commit="c0ffee1234567890",
            )
        )
    done_entry = next((entry for entry in chunk_entries if entry.status == DONE), None)
    if done_entry is not None:
        rows.append(
            compose_artifact(
                chunk_id=done_entry.chunk_id,
                node_id=graph.node(BUILD_NODE_NAME).node_id,
                node_name=BUILD_NODE_NAME,
                epoch=1,
                name="build-log",
                kind=ASSET,
                clock=clock,
                rng=rng,
                content_size=64,
            )
        )
        rows.append(
            compose_artifact(
                chunk_id=done_entry.chunk_id,
                node_id=graph.node(DELIVER_NODE_NAME).node_id,
                node_name=DELIVER_NODE_NAME,
                epoch=1,
                name="release-commit",
                kind=GIT_COMMIT,
                clock=clock,
                rng=rng,
                repo="acme/widget",
                branch="release/1.0",
                commit="deadbeef00001111",
            )
        )

    ceiling_runner_id = chunk_runner_ids[0]
    rows.append(compose_runner_pause(runner_id=ceiling_runner_id, local=True, reason=_CEILING_PAUSE_REASON, set_at=now))

    rows.append(
        compose_event(
            kind="runner.registered",
            severity=INFO,
            message=f"runner {chunk_runner_ids[0]!r} registered",
            runner_id=chunk_runner_ids[0],
            recorded_at=now,
        )
    )
    rows.append(
        compose_event(
            kind="runner.paused",
            severity=WARNING,
            message="runner paused: spend ceiling reached",
            runner_id=ceiling_runner_id,
            recorded_at=now,
        )
    )
    needs_human_entry = next((entry for entry in chunk_entries if entry.status == NEEDS_HUMAN), None)
    if needs_human_entry is not None:
        rows.append(
            compose_event(
                kind="chunk.escalated",
                severity=CRITICAL,
                message="chunk parked for human takeover",
                runner_id=_DEFAULT_EVENT_RUNNER_ID,
                chunk_id=needs_human_entry.chunk_id,
                recorded_at=now,
            )
        )
    rows.append(
        compose_event(
            kind="scenario.seeded",
            severity=INFO,
            message=f"scenario board seeded {chunks} chunk(s)",
            runner_id=_DEFAULT_EVENT_RUNNER_ID,
            recorded_at=now,
        )
    )

    distinct_runner_ids = list(runner_workspaces.keys())
    if stress:
        extras = _compose_stress_extras(graph=graph, clock=clock, rng=rng)
        rows.extend(extras.rows)
        chunk_entries.extend(extras.chunk_entries)
        distinct_runner_ids.append(extras.runner_id)

    status_counts: dict[str, int] = {}
    for entry in chunk_entries:
        status_counts[entry.status] = status_counts.get(entry.status, 0) + 1

    census = BoardCensus(
        chunk_entries=chunk_entries,
        status_counts=status_counts,
        runner_ids=distinct_runner_ids,
        usage_fact_count=usage_fact_count,
        cost_partial_count=cost_partial_count,
        artifact_count=sum(1 for row in rows if row.table == "artifacts"),
        ceiling_paused_runner_id=ceiling_runner_id,
        stress=stress,
        build_node_id=build_node_id,
    )
    return BoardScenario(graph_id=graph.graph_id, rows=rows, census=census)
