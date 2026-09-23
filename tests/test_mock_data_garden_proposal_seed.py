"""Unit coverage for the garden-proposal composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_garden_proposal`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from random import Random

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.hub.garden_proposal_seed import compose_garden_proposal

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_composes_one_garden_proposals_row_citing_no_findings() -> None:
    seeded = compose_garden_proposal(
        clock=FixedClock(_NOW), rng=Random(1), routine_name="nightly", class_="mechanize", title="t", body="b"
    )

    assert len(seeded.rows) == 1
    row = seeded.rows[0]
    assert row.table == "garden_proposals"
    assert row.values["proposal_id"] == seeded.proposal_id
    assert row.values["routine_name"] == "nightly"
    assert row.values["class"] == "mechanize"
    assert row.values["title"] == "t"
    assert row.values["body"] == "b"
    assert row.values["created_at"] == _NOW
    assert row.values["source_artifact_id"] is None
    assert row.values["ref"] is None


def test_mints_a_gprop_prefixed_id() -> None:
    seeded = compose_garden_proposal(
        clock=FixedClock(_NOW), rng=Random(1), routine_name="nightly", class_="mechanize", title="t", body="b"
    )
    assert seeded.proposal_id.startswith("gprop_")


def test_same_seed_mints_the_same_id() -> None:
    first = compose_garden_proposal(
        clock=FixedClock(_NOW), rng=Random(7), routine_name="nightly", class_="mechanize", title="t", body="b"
    )
    second = compose_garden_proposal(
        clock=FixedClock(_NOW), rng=Random(7), routine_name="nightly", class_="mechanize", title="t", body="b"
    )
    assert first.proposal_id == second.proposal_id
