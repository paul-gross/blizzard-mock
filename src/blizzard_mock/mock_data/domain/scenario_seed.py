"""Composes the ``scenario board`` verb's whole ``FactRow`` set (``bzh:domain-core``).

``compose_board_scenario`` mints one synthetic graph, spreads ``--chunks`` chunks
across the nine derived statuses, attaches a realistic cost spread, a
ceiling-paused runner, a runner fleet, and a mixed event log — pure composition
**on top of** Phase 2/3's own composers (``graph_seed``, ``chunk_seed``,
``usage_seed``, ``question_seed``, ``event_seed``, ``runner_pause_seed``); this
module derives no new fact shape of its own, the same discipline
``domain/chunk_seed.py`` already holds for ``lease_seed.compose_lease_row``.

**Status distribution algorithm** (deterministic, so a fixed ``--seed`` composes
an exact, assertable census): :data:`_STATUS_ORDER` lists the nine statuses,
most board-interesting first (``ready``/``running``/``needs_human``/
``waiting_on_human``/``done`` — the five ``--chunks < 9`` must cover at
minimum — then ``paused``/``delivering``/``stopped``/``not_ready``). Chunk
``i`` (0-indexed) always takes ``_STATUS_ORDER[i % len(_STATUS_ORDER)]`` — for
``--chunks < 9`` this is just ``_STATUS_ORDER[i]``, a prefix of the priority
list; for ``--chunks >= 9`` every status is covered at least once by the time
``i`` reaches 8, and any remainder round-robins from the front again. No
randomness in the assignment itself — only id-minting draws from ``rng``.

**Cost spread**: every other chunk (``i % 2 == 0``) gets one ``usage_facts``
row, so the census differs chunk to chunk; chunk 0 (always ``ready`` — first in
:data:`_STATUS_ORDER`, so present whenever ``--chunks >= 1``) is always the
cost-*partial* one (``cost_usd=None``, via ``usage_seed``'s ``--no-cost`` path),
guaranteeing the "at least one cost-partial chunk" requirement independent of
``--chunks``.

**Cap-park vs. ceiling-pause**: this module seeds a **ceiling-paused runner**
(``runner_pause_seed.compose_runner_pause(local=True, ...)``, mirroring
``check_spend_ceiling``'s own reason wording), not a ``--cause cap`` escalation.
A cap escalation only reads back as meaningful on a chunk already at
``needs_human`` (an escalation is what *makes* a chunk ``needs_human`` in the
first place — landing a second one on top is redundant, and landing one on any
other status would silently flip that chunk's derived status out from under
the deterministic census). The ceiling-pause fact is independent of chunk
status entirely, so it composes cleanly alongside the fixed distribution
above.

``--stress`` layers four deliberately extreme properties across three
additional rows on top (see :func:`_compose_stress_extras`): a runner with a
long identity, a chunk with a long custom node name, and one more
``waiting_on_human`` chunk carrying two
*additional* independent question trails (multi-question) beyond the one
``chunk_seed`` itself lands for that status — the same chunk carries both the
"waiting_on_human" and the "multi-question" extremes, deliberately: attaching
extra open questions to a chunk of any *other* status would flip its derived
status (an open question outranks everything but ``stopped``/``done``/
``needs_human`` in ``derive_chunk_status``'s precedence), so keeping them on
one already-``waiting_on_human`` chunk is the only self-consistent way to
combine them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.chunk_seed import (
    DELIVERING,
    DONE,
    NEEDS_HUMAN,
    NOT_READY,
    PAUSED,
    READY,
    RUNNING,
    STOPPED,
    WAITING_ON_HUMAN,
    compose_chunk,
)
from blizzard_mock.mock_data.domain.event_seed import CRITICAL, INFO, WARNING, compose_event
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.graph_seed import (
    BUILD_NODE_NAME,
    DEFAULT_GRAPH_NAME,
    DELIVER_NODE_NAME,
    GraphContext,
    NodeRef,
    compose_graph,
)
from blizzard_mock.mock_data.domain.question_seed import compose_question
from blizzard_mock.mock_data.domain.runner_pause_seed import compose_runner_pause
from blizzard_mock.mock_data.domain.usage_seed import RESUME, SPAWN, compose_usage

#: The nine statuses, board-interesting-first — the priority list
#: :func:`compose_board_scenario`'s deterministic assignment cycles through
#: (module docstring, "Status distribution algorithm").
_STATUS_ORDER: tuple[str, ...] = (
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

#: Deliberately long strings for ``--stress``'s overflow-UI extremes — long
#: enough to blow past any reasonable column/badge width, short enough to stay
#: readable in a test failure message.
_STRESS_LONG_RUNNER_ID = "runner-" + (
    "-".join(["extremely", "long", "runner", "identity", "for", "narrow", "viewport", "checks"]) * 3
)
_STRESS_LONG_NODE_NAME = "custom-" + (
    "-".join(["a", "very", "long", "graph", "node", "name", "for", "overflow", "checks"]) * 3
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
    runner_ids: list[str]
    usage_fact_count: int
    cost_partial_count: int
    ceiling_paused_runner_id: str
    stress: bool

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
    """The same ``runner_registrations`` row shape ``cli.py``'s ``create runner``
    builds inline — Phase 1 kept that verb's own shape rather than a
    ``domain/*_seed.py`` composer, so this module follows the same seam."""
    return FactRow(
        table="runner_registrations",
        values={"runner_id": runner_id, "workspace_id": workspace_id, "registered_at": now, "last_seen_at": now},
    )


def _status_node_id(status: str, graph: GraphContext) -> str:
    """The node a status's own composed transition (if any) lands on — the
    node ``compose_usage``'s attribution attaches its usage fact to. Mirrors
    ``compose_chunk``'s own per-status node defaults (``domain/chunk_seed.py``)."""
    if status in (DELIVERING, DONE):
        return graph.node(DELIVER_NODE_NAME).node_id
    return graph.node(BUILD_NODE_NAME).node_id


def _extend_graph_with_node(graph: GraphContext, name: str, clock: Clock, rng: Random) -> tuple[GraphContext, FactRow]:
    """Mint one extra ``graph_nodes`` row onto ``graph`` — the same row shape
    ``graph_seed.compose_graph`` builds for its own ``build``/``deliver`` nodes,
    parameterized with a caller-chosen ``name`` (``--stress``'s long
    custom-node-name extreme; ``compose_graph`` itself mints only the two fixed
    node names, so this is the one place a third, differently-named node comes
    from). Deliberately mints no ``graph_choices``/``graph_edges`` for it — a
    structural dead end (nothing transitions *out* of this node), harmless
    because nothing in this tool's hydration path validates node reachability."""
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
    """The four ``--stress`` extremes (module docstring): a long-identity
    runner, a chunk landed on a deliberately long custom node name, and one
    ``waiting_on_human`` chunk carrying two extra independent question trails
    (proving Phase 3's "two calls, two independent trails" property holds
    here too)."""
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
) -> BoardScenario:
    """Compose one whole scenario board: a graph, ``chunks`` chunks spread
    across the nine statuses (see the module docstring's distribution
    algorithm), a cost spread with at least one cost-partial usage fact, one
    ceiling-paused runner, a runner per chunk, a mixed event log, and —
    with ``stress=True`` — the four narrow-viewport/overflow extremes.

    Raises :class:`ScenarioCompositionError` for ``chunks < 1``.
    """
    if chunks < 1:
        raise ScenarioCompositionError(f"--chunks must be at least 1, got {chunks}")

    rows: list[FactRow] = []
    minted_graph = compose_graph(graph_name or DEFAULT_GRAPH_NAME, clock, rng)
    rows.extend(minted_graph.rows)
    graph = minted_graph.context
    now = clock.now()

    chunk_entries: list[ChunkCensusEntry] = []
    runner_ids: list[str] = []
    for i in range(chunks):
        status = _STATUS_ORDER[i % len(_STATUS_ORDER)]
        runner_id = f"runner-{i:02d}"
        seeded_chunk = compose_chunk(
            status=status, graph=graph, clock=clock, rng=rng, runner_id=runner_id, workspace_id=f"workspace-{i:02d}"
        )
        rows.extend(seeded_chunk.rows)
        chunk_entries.append(ChunkCensusEntry(chunk_id=seeded_chunk.chunk_id, status=status))
        runner_ids.append(runner_id)

    for i, runner_id in enumerate(runner_ids):
        rows.append(_runner_registration_row(runner_id, f"workspace-{i:02d}", now))

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
                runner_id=runner_ids[i],
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

    ceiling_runner_id = runner_ids[0]
    rows.append(compose_runner_pause(runner_id=ceiling_runner_id, local=True, reason=_CEILING_PAUSE_REASON, set_at=now))

    rows.append(
        compose_event(
            kind="runner.registered",
            severity=INFO,
            message=f"runner {runner_ids[0]!r} registered",
            runner_id=runner_ids[0],
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

    if stress:
        extras = _compose_stress_extras(graph=graph, clock=clock, rng=rng)
        rows.extend(extras.rows)
        chunk_entries.extend(extras.chunk_entries)
        runner_ids.append(extras.runner_id)

    status_counts: dict[str, int] = {}
    for entry in chunk_entries:
        status_counts[entry.status] = status_counts.get(entry.status, 0) + 1

    census = BoardCensus(
        chunk_entries=chunk_entries,
        status_counts=status_counts,
        runner_ids=runner_ids,
        usage_fact_count=usage_fact_count,
        cost_partial_count=cost_partial_count,
        ceiling_paused_runner_id=ceiling_runner_id,
        stress=stress,
    )
    return BoardScenario(graph_id=graph.graph_id, rows=rows, census=census)
