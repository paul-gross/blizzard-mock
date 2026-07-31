"""Composes a synthetic workflow graph's ``FactRow`` set (``bzh:domain-core``).

A freshly provisioned hub's ``graphs`` table is empty — the real hub's own default
graph is minted lazily, on first ingest — so ``create chunk`` needs a graph of its
own to pin a chunk to and transition into. This module mints the **minimal** one: two
nodes, ``build`` (``executor: runner``) and ``deliver`` (``executor: hub``), joined by
one choice/edge each, entry at ``build``, ``deliver``'s own choice reaching the
reserved terminal ``done``. The one required shape constraint (``blizzard/hub/domain/
work.py``'s ``_newest_transition_enters_hub_node``) is the hub node: without one, no
composed chunk could ever derive ``delivering``.

``graphs.definition_yaml`` is audit-only (a mint-if-changed comparison reads it, never
re-parsed to hydrate — the ``graph_nodes``/``graph_choices``/``graph_edges`` rows are
what the hub actually hydrates from), so the inlined text here only needs to read as
valid-enough YAML, not round-trip through the real graph-authoring parser.

:class:`GraphContext` is the **read side** — a graph's shape, however it was
obtained (freshly minted here, or hydrated from an existing store row by
``cli.py``'s ``--graph`` reuse lookup) — that ``domain/chunk_seed.py``'s pure
composer resolves a ``--node`` name against. No ``blizzard`` import (the mock-data
contract's first property): id prefixes are drawn from ``domain/ids.py``'s
independently kept-in-step registry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow

#: The name ``create chunk``/``create graph`` mint under when the caller names none.
DEFAULT_GRAPH_NAME = "mock-data-synthetic-graph"

#: The two node names every graph this module mints carries — ``chunk_seed.py``'s
#: per-status defaults resolve against these, so any graph this tool minted (fresh or
#: reused by name) resolves them the same way.
BUILD_NODE_NAME = "build"
DELIVER_NODE_NAME = "deliver"

_APPROVED_CHOICE_NAME = "approved"
_LANDED_CHOICE_NAME = "landed"
_RESERVED_TERMINAL = "done"  # blizzard.hub.domain.graph.RESERVED_TERMINAL, independently mirrored


class GraphCompositionError(Exception):
    """A ``--node`` name does not resolve against the graph in play."""


@dataclass(frozen=True)
class NodeRef:
    """One graph node's identity and executor facet — what a composer needs to land
    a transition on it and know whether doing so reaches ``delivering``."""

    node_id: str
    executor: str  # "runner" | "hub"


@dataclass(frozen=True)
class GraphContext:
    """A graph's resolved shape — minted fresh, or hydrated from an existing store
    row — keyed by node name, the vocabulary ``--node`` names against."""

    graph_id: str
    entry_node_id: str
    nodes: Mapping[str, NodeRef]

    def node(self, name: str) -> NodeRef:
        """The named node's ref, or a :class:`GraphCompositionError` naming what is known."""
        try:
            return self.nodes[name]
        except KeyError:
            raise GraphCompositionError(
                f"graph {self.graph_id!r} has no node named {name!r} — known nodes: {sorted(self.nodes)}"
            ) from None


@dataclass(frozen=True)
class MintedGraph:
    """A freshly composed graph: its resolved :class:`GraphContext` plus the exact
    ``FactRow``\\ s that mint it (``graphs`` / ``graph_nodes`` / ``graph_choices`` /
    ``graph_edges``, parent-then-child — the store's own FK-safe write reorders these
    regardless, but this is the natural authoring order)."""

    context: GraphContext
    rows: list[FactRow]


def compose_graph(name: str, clock: Clock, rng: Random) -> MintedGraph:
    """Mint one synthetic graph named ``name`` — ``build`` (runner) -> ``deliver``
    (hub) -> the reserved terminal. A pure function of its inputs
    (``bzh:domain-takes-objects``): every id and timestamp is drawn from the
    injected ``clock``/``rng``, never a hidden clock or ``os.urandom`` — the same
    ``--seed`` reproduces the same ids byte-for-byte."""
    graph_id = ids.mint(ids.GRAPH_PREFIX, clock, rng)
    build_id = ids.mint(ids.NODE_PREFIX, clock, rng)
    deliver_id = ids.mint(ids.NODE_PREFIX, clock, rng)
    approved_choice_id = ids.mint(ids.CHOICE_PREFIX, clock, rng)
    landed_choice_id = ids.mint(ids.CHOICE_PREFIX, clock, rng)
    now = clock.now()

    definition_yaml = (
        f"# minted by blizzard-mock-data create graph — audit-only text, see graph_seed.py\n"
        f"name: {name}\n"
        f"entry: {BUILD_NODE_NAME}\n"
        "nodes:\n"
        f"  {BUILD_NODE_NAME}:\n"
        "    executor: runner\n"
        "    judgement:\n"
        "      choices:\n"
        f"        {_APPROVED_CHOICE_NAME}:\n"
        "          description: Work approved, proceed to delivery.\n"
        f"          to: {DELIVER_NODE_NAME}\n"
        f"  {DELIVER_NODE_NAME}:\n"
        "    executor: hub\n"
        "    judgement:\n"
        "      choices:\n"
        f"        {_LANDED_CHOICE_NAME}:\n"
        "          description: Delivered.\n"
        f"          to: {_RESERVED_TERMINAL}\n"
    )

    rows = [
        FactRow(
            table="graphs",
            values={
                "graph_id": graph_id,
                "name": name,
                "entry_node_id": build_id,
                "definition_yaml": definition_yaml,
                "created_at": now,
            },
        ),
        FactRow(
            table="graph_nodes",
            values={
                "node_id": build_id,
                "graph_id": graph_id,
                "name": BUILD_NODE_NAME,
                "executor": "runner",
                "session": "fresh",
                "judged_by": "worker",
            },
        ),
        FactRow(
            table="graph_nodes",
            values={
                "node_id": deliver_id,
                "graph_id": graph_id,
                "name": DELIVER_NODE_NAME,
                "executor": "hub",
                "session": "resume",
                "judged_by": "worker",
            },
        ),
        FactRow(
            table="graph_choices",
            values={
                "choice_id": approved_choice_id,
                "node_id": build_id,
                "name": _APPROVED_CHOICE_NAME,
                "description": "Work approved, proceed to delivery.",
            },
        ),
        FactRow(
            table="graph_choices",
            values={
                "choice_id": landed_choice_id,
                "node_id": deliver_id,
                "name": _LANDED_CHOICE_NAME,
                "description": "Delivered.",
            },
        ),
        FactRow(
            table="graph_edges",
            values={
                "edge_id": f"{build_id}:{approved_choice_id}",
                "from_node_id": build_id,
                "choice_id": approved_choice_id,
                "to_node_name": DELIVER_NODE_NAME,
            },
        ),
        FactRow(
            table="graph_edges",
            values={
                "edge_id": f"{deliver_id}:{landed_choice_id}",
                "from_node_id": deliver_id,
                "choice_id": landed_choice_id,
                "to_node_name": _RESERVED_TERMINAL,
            },
        ),
    ]
    context = GraphContext(
        graph_id=graph_id,
        entry_node_id=build_id,
        nodes={
            BUILD_NODE_NAME: NodeRef(node_id=build_id, executor="runner"),
            DELIVER_NODE_NAME: NodeRef(node_id=deliver_id, executor="hub"),
        },
    )
    return MintedGraph(context=context, rows=rows)


def hydrate_graph_context(graph_row: Mapping[str, object], node_rows: Sequence[Mapping[str, object]]) -> GraphContext:
    """Rebuild a :class:`GraphContext` from an existing ``graphs`` row plus its
    ``graph_nodes`` rows — the read side of ``--graph <name>`` reuse. A pure function
    of already-loaded rows (``bzh:domain-takes-objects``): ``cli.py`` does the actual
    store read (``SeedService.query``), this only shapes what comes back."""
    nodes = {str(row["name"]): NodeRef(node_id=str(row["node_id"]), executor=str(row["executor"])) for row in node_rows}
    return GraphContext(
        graph_id=str(graph_row["graph_id"]),
        entry_node_id=str(graph_row["entry_node_id"]),
        nodes=nodes,
    )
