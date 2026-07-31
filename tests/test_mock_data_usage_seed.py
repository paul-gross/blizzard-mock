"""Unit coverage for the usage-fact composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_usage`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard_mock.mock_data.domain.usage_seed import UsageCompositionError, compose_usage

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_usage_lands_the_supplied_cost() -> None:
    row = compose_usage(
        chunk_id="ch_1",
        node_id="nd_1",
        epoch=2,
        runner_id="r-1",
        kind="spawn",
        model="claude-x",
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=10,
        cache_create_tokens=5,
        cost_usd=1.23,
        recorded_at=_NOW,
    )
    assert row.table == "usage_facts"
    assert row.values["cost_usd"] == 1.23
    assert row.values["chunk_id"] == "ch_1"
    assert row.values["node_id"] == "nd_1"
    assert row.values["epoch"] == 2
    assert row.values["runner_id"] == "r-1"
    assert row.values["kind"] == "spawn"
    assert row.values["input_tokens"] == 100
    assert row.values["output_tokens"] == 50
    assert row.values["cache_read_tokens"] == 10
    assert row.values["cache_create_tokens"] == 5
    assert row.values["recorded_at"] == _NOW


def test_compose_usage_no_cost_lands_a_genuine_none_never_zero() -> None:
    row = compose_usage(
        chunk_id="ch_1",
        node_id="nd_1",
        epoch=1,
        runner_id="r-1",
        kind="resume",
        model="claude-x",
        input_tokens=1,
        cost_usd=None,
        recorded_at=_NOW,
    )
    assert row.values["cost_usd"] is None


def test_compose_usage_defaults_optional_token_counts_to_zero() -> None:
    row = compose_usage(
        chunk_id="ch_1",
        node_id="nd_1",
        epoch=1,
        runner_id="r-1",
        kind="judge",
        model="claude-x",
        input_tokens=1,
        cost_usd=None,
        recorded_at=_NOW,
    )
    assert row.values["output_tokens"] == 0
    assert row.values["cache_read_tokens"] == 0
    assert row.values["cache_create_tokens"] == 0


def test_compose_usage_refuses_an_unknown_kind() -> None:
    with pytest.raises(UsageCompositionError, match="unknown usage kind"):
        compose_usage(
            chunk_id="ch_1",
            node_id="nd_1",
            epoch=1,
            runner_id="r-1",
            kind="bogus",
            model="claude-x",
            input_tokens=1,
            cost_usd=None,
            recorded_at=_NOW,
        )
