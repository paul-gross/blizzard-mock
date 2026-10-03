"""Unit coverage for the gate-decision composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_decision`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.hub.decision_seed import (
    DecisionChoice,
    DecisionCompositionError,
    DecisionSeed,
    compose_decision,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CHOICES = (DecisionChoice("approve", "Ship it"), DecisionChoice("reject"))


def _compose(
    *,
    choices: tuple[DecisionChoice, ...] = _CHOICES,
    imposed_by_runner_id: str | None = None,
    resolved_choice: str | None = None,
    resolved_by: str | None = None,
    rng_seed: int = 1,
) -> DecisionSeed:
    return compose_decision(
        chunk_id="ch_1",
        node_id="nd_1",
        node_name="review",
        clock=FixedClock(_NOW),
        rng=random.Random(rng_seed),
        choices=choices,
        epoch=3,
        imposed_by_runner_id=imposed_by_runner_id,
        resolved_choice=resolved_choice,
        resolved_by=resolved_by,
    )


def test_open_decision_lands_only_the_decisions_row() -> None:
    seed = _compose()
    assert [row.table for row in seed.rows] == ["decisions"]
    values = seed.rows[0].values
    assert values["decision_id"] == seed.decision_id
    assert seed.decision_id.startswith("dec_")
    assert values["chunk_id"] == "ch_1"
    assert values["node_id"] == "nd_1"
    assert values["node_name"] == "review"
    assert values["epoch"] == 3
    assert values["submitted_at"] == _NOW
    assert values["imposed_by_runner_id"] is None
    assert json.loads(str(values["choices"])) == [
        {"name": "approve", "description": "Ship it"},
        {"name": "reject", "description": ""},
    ]


def test_runner_imposed_decision_records_the_runner() -> None:
    seed = _compose(imposed_by_runner_id="r-gate")
    assert seed.rows[0].values["imposed_by_runner_id"] == "r-gate"


def test_resolution_lands_a_decision_resolutions_row() -> None:
    seed = _compose(resolved_choice="reject", resolved_by="operator-1")
    assert [row.table for row in seed.rows] == ["decisions", "decision_resolutions"]
    values = seed.rows[1].values
    assert values["decision_id"] == seed.decision_id
    assert values["choice"] == "reject"
    assert values["resolved_by"] == "operator-1"
    assert values["resolved_at"] > _NOW  # type: ignore[operator]


def test_two_calls_mint_independent_decision_ids() -> None:
    assert _compose(rng_seed=1).decision_id != _compose(rng_seed=2).decision_id


def test_no_choices_is_refused() -> None:
    with pytest.raises(DecisionCompositionError, match="at least one"):
        _compose(choices=())


def test_duplicate_choice_names_are_refused() -> None:
    with pytest.raises(DecisionCompositionError, match="unique"):
        _compose(choices=(DecisionChoice("a"), DecisionChoice("a")))


def test_resolving_an_unoffered_choice_is_refused() -> None:
    with pytest.raises(DecisionCompositionError, match="not one of"):
        _compose(resolved_choice="maybe", resolved_by="operator-1")


def test_resolve_without_resolver_is_refused() -> None:
    with pytest.raises(DecisionCompositionError, match="together"):
        _compose(resolved_choice="approve")


def test_resolver_without_resolve_is_refused() -> None:
    with pytest.raises(DecisionCompositionError, match="together"):
        _compose(resolved_by="operator-1")
