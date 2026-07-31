"""Unit coverage for the ``scenario board`` composer (``blizzard-mock:unit-test``).

Pure, no store: :func:`compose_board_scenario` is a plain function over an
injected ``clock``/``rng`` (``bzh:domain-takes-objects``), composing purely on
top of Phase 2/3's own composers. Exercises the deterministic status
distribution, the cost spread, the ceiling-pause choice, the ``--stress``
extremes, and ``--seed`` reproducibility.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import UTC, datetime

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.scenario_seed import ScenarioCompositionError, compose_board_scenario

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _rows_for(rows: list[FactRow], table: str) -> list[FactRow]:
    return [row for row in rows if row.table == table]


def _compose(chunks: int, *, stress: bool = False, seed: int = 1):
    return compose_board_scenario(chunks=chunks, clock=FixedClock(_NOW), rng=random.Random(seed), stress=stress)


# --- status distribution -----------------------------------------------------


def test_default_six_chunks_cover_the_five_required_statuses_plus_one() -> None:
    scenario = _compose(6)
    assert scenario.census is not None
    statuses = [entry.status for entry in scenario.census.chunk_entries]
    assert statuses == ["ready", "running", "needs_human", "waiting_on_human", "done", "paused"]
    assert scenario.census.status_counts == dict(Counter(statuses))


def test_fewer_than_nine_chunks_is_a_prefix_of_the_priority_order() -> None:
    scenario = _compose(3)
    assert scenario.census is not None
    statuses = [entry.status for entry in scenario.census.chunk_entries]
    assert statuses == ["ready", "running", "needs_human"]


def test_nine_or_more_chunks_covers_every_status_and_round_robins_the_remainder() -> None:
    scenario = _compose(12)
    assert scenario.census is not None
    statuses = [entry.status for entry in scenario.census.chunk_entries]
    assert len(statuses) == 12
    assert set(statuses) == {
        "stopped",
        "done",
        "needs_human",
        "waiting_on_human",
        "paused",
        "delivering",
        "running",
        "not_ready",
        "ready",
    }
    # the remainder (chunks 9, 10, 11) round-robins from the front of the priority order again
    assert statuses[9:] == statuses[0:3]


def test_chunks_below_one_is_refused() -> None:
    with pytest.raises(ScenarioCompositionError, match="--chunks"):
        _compose(0)


# --- graph -----------------------------------------------------------------


def test_composes_exactly_one_graph() -> None:
    scenario = _compose(6)
    assert len(_rows_for(scenario.rows, "graphs")) == 1
    assert scenario.graph_id


# --- cost spread -------------------------------------------------------------


def test_at_least_one_usage_fact_is_cost_partial() -> None:
    scenario = _compose(6)
    usage_rows = _rows_for(scenario.rows, "usage_facts")
    assert len(usage_rows) >= 2
    assert any(row.values["cost_usd"] is None for row in usage_rows)
    costs = {row.values["cost_usd"] for row in usage_rows if row.values["cost_usd"] is not None}
    assert len(costs) >= 1  # varying, non-null costs alongside the partial one


def test_census_reports_the_cost_partial_count() -> None:
    scenario = _compose(6)
    assert scenario.census is not None
    assert scenario.census.cost_partial_count >= 1
    assert scenario.census.usage_fact_count >= scenario.census.cost_partial_count


# --- workspace attribution ----------------------------------------------------


def test_every_seeded_route_claims_a_workspace_its_own_runner_is_registered_under() -> None:
    """Regression: every ``running``/``delivering`` chunk's ``route_created`` row used
    to hardcode the same ``workspace-seed`` (``chunk_seed.py``'s own CLI-verb default)
    regardless of which runner it was attributed to, while the runner fleet this
    module registers uses ``workspace-{i:02d}`` — so a route's claimed workspace and
    its own runner's registered workspace silently disagreed."""
    scenario = _compose(9, stress=True)
    registrations = {
        row.values["runner_id"]: row.values["workspace_id"] for row in _rows_for(scenario.rows, "runner_registrations")
    }
    route_rows = _rows_for(scenario.rows, "route_created")
    assert route_rows  # --chunks 9 covers running/delivering, so at least one route lands
    for route in route_rows:
        runner_id = route.values["runner_id"]
        assert route.values["workspace_id"] == registrations[runner_id]


# --- ceiling-paused runner ----------------------------------------------------


def test_a_runner_is_ceiling_paused_with_a_reason() -> None:
    scenario = _compose(6)
    assert scenario.census is not None
    pause_rows = _rows_for(scenario.rows, "runner_local_pause_facts")
    assert len(pause_rows) == 1
    pause_row = pause_rows[0]
    assert pause_row.values["runner_id"] == scenario.census.ceiling_paused_runner_id
    assert pause_row.values["reason"]
    assert "ceiling" in str(pause_row.values["reason"])


# --- --stress extremes --------------------------------------------------------


def test_stress_off_by_default() -> None:
    scenario = _compose(6, stress=False)
    assert scenario.census is not None
    assert scenario.census.stress is False
    assert scenario.census.chunk_count == 6


def test_stress_adds_a_long_identity_runner() -> None:
    scenario = _compose(6, stress=True)
    registration_rows = _rows_for(scenario.rows, "runner_registrations")
    long_ids = [row.values["runner_id"] for row in registration_rows if len(str(row.values["runner_id"])) > 100]
    assert long_ids, "expected a deliberately long runner_id among the registrations"


def test_stress_adds_a_waiting_on_human_chunk() -> None:
    base = _compose(6, stress=False)
    stressed = _compose(6, stress=True)
    assert base.census is not None
    assert stressed.census is not None
    base_waiting = base.census.status_counts.get("waiting_on_human", 0)
    stressed_waiting = stressed.census.status_counts.get("waiting_on_human", 0)
    assert stressed_waiting == base_waiting + 1


def test_stress_adds_a_long_custom_node_name() -> None:
    scenario = _compose(6, stress=True)
    node_rows = _rows_for(scenario.rows, "graph_nodes")
    long_names = [row.values["name"] for row in node_rows if len(str(row.values["name"])) > 100]
    assert long_names, "expected a deliberately long graph_nodes.name among the stress rows"
    transition_rows = _rows_for(scenario.rows, "transitions")
    node_ids_by_name = {row.values["name"]: row.values["node_id"] for row in node_rows}
    long_node_id = node_ids_by_name[long_names[0]]
    assert any(row.values["to_node_id"] == long_node_id for row in transition_rows)


def test_stress_adds_a_multi_question_chunk() -> None:
    scenario = _compose(6, stress=True)
    question_rows = _rows_for(scenario.rows, "questions")
    counts = Counter(row.values["chunk_id"] for row in question_rows)
    assert any(count >= 2 for count in counts.values()), "expected one chunk to carry 2+ distinct question ids"
    # every question id under that chunk is distinct
    multi_chunk_id = next(chunk_id for chunk_id, count in counts.items() if count >= 2)
    ids_for_chunk = [row.values["question_id"] for row in question_rows if row.values["chunk_id"] == multi_chunk_id]
    assert len(ids_for_chunk) == len(set(ids_for_chunk))


def test_stress_grows_the_chunk_and_runner_counts() -> None:
    base = _compose(6, stress=False)
    stressed = _compose(6, stress=True)
    assert base.census is not None
    assert stressed.census is not None
    assert stressed.census.chunk_count == base.census.chunk_count + 2
    assert stressed.census.runner_count == base.census.runner_count + 1


# --- reproducibility -----------------------------------------------------------


def test_compose_board_scenario_is_reproducible_with_the_same_seed() -> None:
    a = _compose(6, stress=True, seed=42)
    b = _compose(6, stress=True, seed=42)
    assert a.graph_id == b.graph_id
    assert a.rows == b.rows
    assert a.census == b.census


def test_compose_board_scenario_differs_with_a_different_seed() -> None:
    a = _compose(6, seed=1)
    b = _compose(6, seed=2)
    assert a.census is not None
    assert b.census is not None
    assert a.graph_id != b.graph_id
    a_ids = [entry.chunk_id for entry in a.census.chunk_entries]
    b_ids = [entry.chunk_id for entry in b.census.chunk_entries]
    assert a_ids != b_ids
