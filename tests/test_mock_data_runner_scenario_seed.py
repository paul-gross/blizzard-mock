"""Unit coverage for the runner-store fleet mirror (``blizzard-mock:unit-test``).

Pure, no store: ``compose_runner_fleet`` is a plain function over an already-composed
hub :class:`BoardCensus` (``bzh:domain-takes-objects``) — the census is never re-read
from a store, just handed straight through.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.hub.scenario_seed import compose_board_scenario
from blizzard_mock.mock_data.domain.runner.scenario_seed import RunnerFleetCompositionError, compose_runner_fleet

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_RUNNER_ID = "runner-local"


def _fleet(chunks: int = 6, *, seed: int = 1):
    clock = FixedClock(_NOW)
    rng = random.Random(seed)
    hub = compose_board_scenario(chunks=chunks, clock=clock, rng=rng, runner_id=_RUNNER_ID)
    assert hub.census is not None
    census = hub.census
    fleet = compose_runner_fleet(census=census, graph_id=hub.graph_id, runner_id=_RUNNER_ID, clock=clock, rng=rng)
    return census, fleet


def _rows_for(rows, table: str):
    return [row for row in rows if row.table == table]


def test_refuses_a_census_with_too_few_chunks_to_reach_both_dormant_shapes() -> None:
    clock = FixedClock(_NOW)
    rng = random.Random(1)
    hub = compose_board_scenario(chunks=1, clock=clock, rng=rng, runner_id=_RUNNER_ID)
    assert hub.census is not None
    with pytest.raises(RunnerFleetCompositionError, match="waiting_on_human/needs_human"):
        compose_runner_fleet(census=hub.census, graph_id=hub.graph_id, runner_id=_RUNNER_ID, clock=clock, rng=rng)


def test_mirrors_exactly_two_leases_under_the_pinned_runner() -> None:
    _census, fleet = _fleet()
    lease_rows = _rows_for(fleet.rows, "leases")
    assert len(lease_rows) == 2
    assert {row.values["runner_id"] for row in lease_rows} == {_RUNNER_ID}


def test_every_lease_carries_its_lease_context_sibling() -> None:
    _census, fleet = _fleet()
    lease_ids = {row.values["lease_id"] for row in _rows_for(fleet.rows, "leases")}
    context_ids = {row.values["lease_id"] for row in _rows_for(fleet.rows, "lease_context")}
    assert lease_ids == context_ids


def test_the_waiting_on_human_chunk_gets_an_active_lease_parked_on_an_ask() -> None:
    census, fleet = _fleet()
    waiting_entry = next(e for e in census.chunk_entries if e.status == "waiting_on_human")
    waiting_lease = next(
        row for row in _rows_for(fleet.rows, "leases") if row.values["chunk_id"] == waiting_entry.chunk_id
    )
    assert waiting_lease.values["pid"] is None
    assert waiting_lease.values["session_id"] is None
    closures = {row.values["lease_id"] for row in _rows_for(fleet.rows, "lease_closures")}
    assert waiting_lease.values["lease_id"] not in closures
    ask_rows = _rows_for(fleet.rows, "asks")
    assert any(row.values["lease_id"] == waiting_lease.values["lease_id"] for row in ask_rows)
    assert not _rows_for(fleet.rows, "park_resumes")


def test_the_needs_human_chunk_gets_a_closed_escalated_lease_under_an_open_takeover() -> None:
    census, fleet = _fleet()
    needs_human_entry = next(e for e in census.chunk_entries if e.status == "needs_human")
    lease = next(row for row in _rows_for(fleet.rows, "leases") if row.values["chunk_id"] == needs_human_entry.chunk_id)
    assert lease.values["session_id"] is not None
    closures = [
        row for row in _rows_for(fleet.rows, "lease_closures") if row.values["lease_id"] == lease.values["lease_id"]
    ]
    assert len(closures) == 1
    assert closures[0].values["reason"] == "escalated"
    takeovers = [
        row for row in _rows_for(fleet.rows, "takeovers") if row.values["chunk_id"] == needs_human_entry.chunk_id
    ]
    assert len(takeovers) == 1
    assert takeovers[0].values["fence_epoch"] is None


def test_both_mirrored_chunks_carry_an_env_binding() -> None:
    census, fleet = _fleet()
    mirrored_ids = {e.chunk_id for e in census.chunk_entries if e.status in ("waiting_on_human", "needs_human")}
    bound_ids = {row.values["chunk_id"] for row in _rows_for(fleet.rows, "env_bindings")}
    assert bound_ids == mirrored_ids


def test_the_runners_local_pause_lands_engaged() -> None:
    _census, fleet = _fleet()
    pause_rows = _rows_for(fleet.brake, "local_pause_facts")
    assert len(pause_rows) == 1
    assert pause_rows[0].values["runner_id"] == _RUNNER_ID
    assert pause_rows[0].values["paused"] is True


def test_the_brake_is_separate_from_the_rest_of_the_mirror() -> None:
    """The write order ``scenario fleet`` depends on is a property of the composed
    value, not a table-name filter the composition root re-derives."""
    _census, fleet = _fleet()
    assert [row.table for row in fleet.brake] == ["local_pause_facts"]
    assert not _rows_for(fleet.rows, "local_pause_facts")


def test_every_outbound_fact_lands_pre_acked() -> None:
    _census, fleet = _fleet()
    fact_rows = _rows_for(fleet.rows, "outbound_buffer")
    assert fact_rows
    assert all(row.values["acked_at"] is not None for row in fact_rows)


def test_no_running_chunk_is_mirrored() -> None:
    census, fleet = _fleet()
    running_entry = next(e for e in census.chunk_entries if e.status == "running")
    lease_chunk_ids = {row.values["chunk_id"] for row in _rows_for(fleet.rows, "leases")}
    assert running_entry.chunk_id not in lease_chunk_ids


def test_compose_runner_fleet_is_reproducible_with_the_same_seed() -> None:
    _census_a, fleet_a = _fleet(seed=42)
    _census_b, fleet_b = _fleet(seed=42)
    assert fleet_a.rows == fleet_b.rows
