"""Unit coverage for the runner-pause composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_runner_pause`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard_mock.mock_data.domain.runner_pause_seed import RunnerPauseCompositionError, compose_runner_pause

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_local_lands_a_runner_local_pause_facts_row_carrying_the_reason() -> None:
    row = compose_runner_pause(runner_id="r-1", local=True, reason="spend ceiling reached", set_at=_NOW)
    assert row.table == "runner_local_pause_facts"
    assert row.values == {
        "runner_id": "r-1",
        "paused": True,
        "set_at": _NOW,
        "set_by": "mock-data",
        "reason": "spend ceiling reached",
    }


def test_local_without_a_reason_lands_none() -> None:
    row = compose_runner_pause(runner_id="r-1", local=True, set_at=_NOW)
    assert row.values["reason"] is None


def test_fleet_lands_a_runner_pause_facts_row_with_no_reason_column() -> None:
    row = compose_runner_pause(runner_id="r-1", local=False, set_at=_NOW)
    assert row.table == "runner_pause_facts"
    assert row.values == {"runner_id": "r-1", "paused": True, "set_at": _NOW, "set_by": "mock-data"}
    assert "reason" not in row.values


def test_fleet_with_a_reason_fails_loud_naming_the_missing_column() -> None:
    with pytest.raises(RunnerPauseCompositionError, match="runner_pause_facts"):
        compose_runner_pause(runner_id="r-1", local=False, reason="oops", set_at=_NOW)
