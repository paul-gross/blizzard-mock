"""Unit coverage for the runner-store local-pause composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_local_pause`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``), always engaged.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.runner.local_pause_seed import compose_local_pause

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_local_pause_lands_engaged() -> None:
    row = compose_local_pause(runner_id="runner-1", set_at=_NOW)
    assert row.table == "local_pause_facts"
    assert row.values["paused"] is True
    assert row.values["runner_id"] == "runner-1"
    assert row.values["set_by"] == "mock-data"


def test_compose_local_pause_carries_no_reason_column() -> None:
    row = compose_local_pause(runner_id="runner-1", set_at=_NOW)
    assert "reason" not in row.values
