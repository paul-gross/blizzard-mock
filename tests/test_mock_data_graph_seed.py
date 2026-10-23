"""Unit coverage for the synthetic graph composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_graph``/``hydrate_graph_context`` are plain functions over
already-loaded data (``bzh:domain-takes-objects``), so this exercises them directly.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any

import pytest
import yaml

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.hub.graph_seed import (
    BUILD_NODE_NAME,
    DELIVER_NODE_NAME,
    LAND_ACTION,
    GraphCompositionError,
    MintedGraph,
    compose_graph,
    hydrate_graph_context,
)
from blizzard_mock.mock_data.domain.schema_contract import SchemaDriftError

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_graph_mints_a_runner_node_and_a_hub_node() -> None:
    minted = compose_graph("my-graph", FixedClock(_NOW), random.Random(1), has_action_column=False)
    tables = [row.table for row in minted.rows]
    assert tables.count("graphs") == 1
    assert tables.count("graph_nodes") == 2
    assert tables.count("graph_choices") == 5
    assert tables.count("graph_edges") == 5

    assert minted.context.nodes[BUILD_NODE_NAME].executor == "runner"
    assert minted.context.nodes[DELIVER_NODE_NAME].executor == "hub"
    assert minted.context.entry_node_id == minted.context.nodes[BUILD_NODE_NAME].node_id

    graph_row = next(row for row in minted.rows if row.table == "graphs")
    assert graph_row.values["name"] == "my-graph"
    assert graph_row.values["entry_node_id"] == minted.context.entry_node_id


def test_compose_graph_edges_land_build_into_deliver_into_the_terminal() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(1), has_action_column=False)
    build = minted.context.nodes[BUILD_NODE_NAME]
    deliver = minted.context.nodes[DELIVER_NODE_NAME]
    edges = [row for row in minted.rows if row.table == "graph_edges"]
    from_build = next(e for e in edges if e.values["from_node_id"] == build.node_id)
    assert from_build.values["to_node_name"] == DELIVER_NODE_NAME
    from_deliver = next(e for e in edges if e.values["from_node_id"] == deliver.node_id)
    assert from_deliver.values["to_node_name"] == "done"


def test_compose_graph_is_reproducible_with_the_same_seed() -> None:
    a = compose_graph("g", FixedClock(_NOW), random.Random(42), has_action_column=False)
    b = compose_graph("g", FixedClock(_NOW), random.Random(42), has_action_column=False)
    assert a.context == b.context
    assert a.rows == b.rows


def test_compose_graph_differs_with_a_different_seed() -> None:
    a = compose_graph("g", FixedClock(_NOW), random.Random(1), has_action_column=False)
    b = compose_graph("g", FixedClock(_NOW), random.Random(2), has_action_column=False)
    assert a.context.graph_id != b.context.graph_id


def test_graph_context_node_raises_for_an_unknown_name() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(1), has_action_column=False)
    with pytest.raises(GraphCompositionError, match="unknown-node"):
        minted.context.node("unknown-node")


def test_hydrate_graph_context_round_trips_a_minted_graph() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(7), has_action_column=False)
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
    minted = compose_graph("g", FixedClock(_NOW), random.Random(7), has_action_column=False)
    graph_row = {"graph_id": minted.context.graph_id, "entry_node_id": minted.context.entry_node_id}
    node_rows = [{"name": "build", "node_id": "gn_1"}]  # missing "executor" — a drifted graph_nodes row
    with pytest.raises(SchemaDriftError, match=r"graph_nodes.*executor"):
        hydrate_graph_context(graph_row, node_rows)


def _deliver_routes(minted: MintedGraph) -> dict[str, str]:
    """``deliver``'s choice names, each mapped to the node its edge lands on."""
    deliver_id = minted.context.nodes[DELIVER_NODE_NAME].node_id
    names = {
        row.values["choice_id"]: row.values["name"]
        for row in minted.rows
        if row.table == "graph_choices" and row.values["node_id"] == deliver_id
    }
    return {
        str(names[row.values["choice_id"]]): str(row.values["to_node_name"])
        for row in minted.rows
        if row.table == "graph_edges" and row.values["from_node_id"] == deliver_id
    }


def _deliver_row(minted: MintedGraph) -> dict[str, object]:
    return next(
        dict(row.values)
        for row in minted.rows
        if row.table == "graph_nodes" and row.values["name"] == DELIVER_NODE_NAME
    )


def _definition_yaml(minted: MintedGraph) -> dict[str, Any]:
    graph_row = next(row for row in minted.rows if row.table == "graphs")
    return yaml.safe_load(str(graph_row.values["definition_yaml"]))


@pytest.mark.parametrize("has_action_column", [True, False])
def test_compose_graph_routes_every_land_answer(has_action_column: bool) -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(3), has_action_column=has_action_column)
    expected = {"landed": "done", "conflict": BUILD_NODE_NAME, "failure": BUILD_NODE_NAME}
    expected["inherited-failure"] = BUILD_NODE_NAME
    assert _deliver_routes(minted) == expected
    choices = _definition_yaml(minted)["nodes"][DELIVER_NODE_NAME]["judgement"]["choices"]
    assert {name: choice["to"] for name, choice in choices.items()} == expected


def test_compose_graph_names_land_in_the_row_and_the_yaml_when_the_schema_has_action() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(3), has_action_column=True)
    row = _deliver_row(minted)
    assert row["action"] == LAND_ACTION == "land"
    assert "action_args" in row and row["action_args"] is None
    deliver = _definition_yaml(minted)["nodes"][DELIVER_NODE_NAME]
    assert deliver["action"] == "land"
    assert "run" not in deliver


def test_compose_graph_keeps_the_empty_run_shape_when_the_schema_lacks_action() -> None:
    minted = compose_graph("g", FixedClock(_NOW), random.Random(3), has_action_column=False)
    row = _deliver_row(minted)
    assert "action" not in row and "action_args" not in row
    deliver = _definition_yaml(minted)["nodes"][DELIVER_NODE_NAME]
    assert "action" not in deliver and "run" not in deliver
    build = next(r.values for r in minted.rows if r.table == "graph_nodes" and r.values["name"] == BUILD_NODE_NAME)
    assert "action" not in build
