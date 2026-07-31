"""Unit coverage for the synthetic graph composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_graph``/``hydrate_graph_context`` are plain functions over
already-loaded data (``bzh:domain-takes-objects``), so this exercises them directly.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.graph_seed import (
    BUILD_NODE_NAME,
    DELIVER_NODE_NAME,
    GraphCompositionError,
    compose_graph,
    hydrate_graph_context,
)
from blizzard_mock.mock_data.domain.schema_contract import SchemaDriftError

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_graph_mints_a_runner_node_and_a_hub_node() -> None:
    minted = compose_graph("my-graph", FixedClock(_NOW), random.Random(1))
    tables = [row.table for row in minted.rows]
    assert tables.count("graphs") == 1
    assert tables.count("graph_nodes") == 2
    assert tables.count("graph_choices") == 2
    assert tables.count("graph_edges") == 2

    assert minted.context.nodes[BUILD_NODE_NAME].executor == "runner"
    assert minted.context.nodes[DELIVER_NODE_NAME].executor == "hub"
    assert minted.context.entry_node_id == minted.context.nodes[BUILD_NODE_NAME].node_id

    graph_row = next(row for row in minted.rows if row.table == "graphs")
    assert graph_row.values["name"] == "my-graph"
    assert graph_row.values["entry_node_id"] == minted.context.entry_node_id


def test_compose_graph_edges_land_build_into_deliver_into_the_terminal() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(1))
    build = minted.context.nodes[BUILD_NODE_NAME]
    deliver = minted.context.nodes[DELIVER_NODE_NAME]
    edges = [row for row in minted.rows if row.table == "graph_edges"]
    from_build = next(e for e in edges if e.values["from_node_id"] == build.node_id)
    assert from_build.values["to_node_name"] == DELIVER_NODE_NAME
    from_deliver = next(e for e in edges if e.values["from_node_id"] == deliver.node_id)
    assert from_deliver.values["to_node_name"] == "done"


def test_compose_graph_is_reproducible_with_the_same_seed() -> None:
    a = compose_graph("g", FixedClock(_NOW), random.Random(42))
    b = compose_graph("g", FixedClock(_NOW), random.Random(42))
    assert a.context == b.context
    assert a.rows == b.rows


def test_compose_graph_differs_with_a_different_seed() -> None:
    a = compose_graph("g", FixedClock(_NOW), random.Random(1))
    b = compose_graph("g", FixedClock(_NOW), random.Random(2))
    assert a.context.graph_id != b.context.graph_id


def test_graph_context_node_raises_for_an_unknown_name() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(1))
    with pytest.raises(GraphCompositionError, match="unknown-node"):
        minted.context.node("unknown-node")


def test_hydrate_graph_context_round_trips_a_minted_graph() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(7))
    graph_row = {"graph_id": minted.context.graph_id, "entry_node_id": minted.context.entry_node_id}
    node_rows = [
        {"name": name, "node_id": ref.node_id, "executor": ref.executor} for name, ref in minted.context.nodes.items()
    ]
    hydrated = hydrate_graph_context(graph_row, node_rows)
    assert hydrated == minted.context


def test_hydrate_graph_context_raises_schema_drift_not_key_error_for_a_missing_column() -> None:
    """Regression: a row missing a column ``hydrate_graph_context`` reads by name
    (a live schema drift on the *read* seam — the drift guard only ever ran on the
    write path) used to raise a bare ``KeyError`` instead of the tool's own promised
    ``SchemaDriftError`` naming the table and column."""
    minted = compose_graph("g", FixedClock(_NOW), random.Random(7))
    graph_row = {"graph_id": minted.context.graph_id, "entry_node_id": minted.context.entry_node_id}
    node_rows = [{"name": "build", "node_id": "gn_1"}]  # missing "executor" — a drifted graph_nodes row
    with pytest.raises(SchemaDriftError, match=r"graph_nodes.*executor"):
        hydrate_graph_context(graph_row, node_rows)
