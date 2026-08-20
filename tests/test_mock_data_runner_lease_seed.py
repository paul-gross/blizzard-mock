"""Unit coverage for the runner-store lease composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_lease`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``) that always returns the ``leases`` row paired with
its ``lease_context`` sibling — never one without the other.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.ids import seeded_rng
from blizzard_mock.mock_data.domain.runner.lease_seed import ESCALATED, compose_lease, compose_lease_closure

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_lease_always_lands_both_rows() -> None:
    seeded = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=2,
        graph_id="gr_1",
        node_id="build",
        node_name="build",
        retries_max=3,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
    )
    tables = [row.table for row in seeded.rows]
    assert tables == ["leases", "lease_context"]


def test_compose_lease_shares_one_minted_lease_id_across_both_rows() -> None:
    seeded = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=1,
        graph_id="gr_1",
        node_id="build",
        node_name="build",
        retries_max=3,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
    )
    lease_row, context_row = seeded.rows
    assert lease_row.values["lease_id"] == seeded.lease_id
    assert context_row.values["lease_id"] == seeded.lease_id
    assert seeded.lease_id.startswith("lease_")


def test_compose_lease_lands_null_pid_session_before_any_spawn_return() -> None:
    seeded = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=1,
        graph_id="gr_1",
        node_id="build",
        node_name="build",
        retries_max=3,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
    )
    lease_row, _context_row = seeded.rows
    assert lease_row.values["pid"] is None
    assert lease_row.values["process_start_time"] is None
    assert lease_row.values["session_id"] is None


def test_compose_lease_lands_the_supplied_node_context() -> None:
    seeded = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=4,
        graph_id="gr_1",
        node_id="deliver",
        node_name="deliver",
        retries_max=5,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
    )
    _lease_row, context_row = seeded.rows
    assert context_row.values["graph_id"] == "gr_1"
    assert context_row.values["node_id"] == "deliver"
    assert context_row.values["node_name"] == "deliver"
    assert context_row.values["retries_max"] == 5


def test_compose_lease_same_seed_mints_the_same_id() -> None:
    first = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=1,
        graph_id="gr_1",
        node_id="build",
        node_name="build",
        retries_max=3,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(7),
    )
    second = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=1,
        graph_id="gr_1",
        node_id="build",
        node_name="build",
        retries_max=3,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(7),
    )
    assert first.lease_id == second.lease_id


def test_compose_lease_accepts_a_supplied_session_id_with_no_pid() -> None:
    """A dormant, escalated mirror needs a resumable session with no live worker —
    ``pid``/``process_start_time`` still land ``NULL`` even when ``session_id`` is given."""
    seeded = compose_lease(
        chunk_id="ch_1",
        runner_id="r-1",
        epoch=1,
        graph_id="gr_1",
        node_id="build",
        node_name="build",
        retries_max=3,
        created_at=_NOW,
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
        session_id="sess_1",
    )
    lease_row, _context_row = seeded.rows
    assert lease_row.values["session_id"] == "sess_1"
    assert lease_row.values["pid"] is None
    assert lease_row.values["process_start_time"] is None


def test_compose_lease_closure_lands_the_reason() -> None:
    row = compose_lease_closure(lease_id="lease_1", chunk_id="ch_1", node_id="build", reason=ESCALATED, closed_at=_NOW)
    assert row.table == "lease_closures"
    assert row.values["reason"] == "escalated"
    assert row.values["lease_id"] == "lease_1"
    assert row.values["chunk_id"] == "ch_1"
