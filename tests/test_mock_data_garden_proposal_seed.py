"""Unit coverage for the garden-proposal composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_garden_proposal`` is a plain function over already-loaded
data (``bzh:domain-takes-objects``).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.hub.garden_proposal_seed import (
    GardenProposalCompositionError,
    GardenProposalSeed,
    compose_garden_proposal,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _compose(
    *,
    title: str | None = None,
    body: str | None = None,
    created_at: datetime | None = None,
    closure: str | None = None,
    closed_by: str = "seed-operator",
) -> GardenProposalSeed:
    return compose_garden_proposal(
        clock=FixedClock(_NOW),
        rng=random.Random(1),
        routine_name="triage",
        class_="hygiene",
        title=title,
        body=body,
        created_at=created_at,
        closure=closure,
        closed_by=closed_by,
    )


def test_open_proposal_lands_only_the_garden_proposals_row() -> None:
    seed = _compose()
    assert [row.table for row in seed.rows] == ["garden_proposals"]
    row = seed.rows[0]
    assert row.values["proposal_id"] == seed.proposal_id
    assert row.values["routine_name"] == "triage"
    assert row.values["class"] == "hygiene"
    assert row.values["created_at"] == _NOW
    assert row.values["source_artifact_id"] is None
    assert row.values["ref"] is None


def test_title_and_body_default_to_derived_boilerplate() -> None:
    row = _compose().rows[0]
    assert row.values["title"]
    assert row.values["body"]
    assert "triage" in str(row.values["title"])
    assert "hygiene" in str(row.values["body"])


def test_title_and_body_are_overridable() -> None:
    row = _compose(title="Custom title", body="Custom body").rows[0]
    assert row.values["title"] == "Custom title"
    assert row.values["body"] == "Custom body"


def test_created_at_defaults_to_the_clock() -> None:
    row = _compose().rows[0]
    assert row.values["created_at"] == _NOW


def test_created_at_override_lands_verbatim() -> None:
    outside_window = datetime(2020, 1, 1, tzinfo=UTC)
    row = _compose(created_at=outside_window).rows[0]
    assert row.values["created_at"] == outside_window


def test_closure_passed_lands_a_closures_row_with_no_item_outcome() -> None:
    seed = _compose(closure="passed")
    assert [row.table for row in seed.rows] == ["garden_proposals", "garden_proposal_closures"]
    closure_row = seed.rows[1]
    assert closure_row.values["proposal_id"] == seed.proposal_id
    assert closure_row.values["closure"] == "passed"
    assert closure_row.values["item_outcome"] is None
    assert closure_row.values["closed_by"] == "seed-operator"
    assert closure_row.values["closed_at"] == _NOW + timedelta(seconds=1)


def test_closure_accepted_minted_lands_the_minted_item_outcome() -> None:
    seed = _compose(closure="accepted-minted")
    closure_row = seed.rows[1]
    assert closure_row.values["closure"] == "accepted"
    assert closure_row.values["item_outcome"] == "minted"


def test_closure_accepted_declined_lands_the_declined_item_outcome() -> None:
    seed = _compose(closure="accepted-declined")
    closure_row = seed.rows[1]
    assert closure_row.values["closure"] == "accepted"
    assert closure_row.values["item_outcome"] == "declined"


def test_closed_at_follows_an_explicit_created_at_override() -> None:
    outside_window = datetime(2020, 1, 1, tzinfo=UTC)
    seed = _compose(created_at=outside_window, closure="passed")
    closure_row = seed.rows[1]
    assert closure_row.values["closed_at"] == outside_window + timedelta(seconds=1)


def test_closed_by_is_overridable() -> None:
    seed = _compose(closure="passed", closed_by="operator-1")
    assert seed.rows[1].values["closed_by"] == "operator-1"


def test_unknown_closure_is_refused() -> None:
    with pytest.raises(GardenProposalCompositionError, match="unknown closure"):
        _compose(closure="bogus")


def test_two_calls_mint_independent_non_colliding_proposal_ids() -> None:
    a = compose_garden_proposal(clock=FixedClock(_NOW), rng=random.Random(1), routine_name="triage", class_="hygiene")
    b = compose_garden_proposal(clock=FixedClock(_NOW), rng=random.Random(2), routine_name="triage", class_="hygiene")
    assert a.proposal_id != b.proposal_id
