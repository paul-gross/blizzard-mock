"""Unit coverage for the per-slug external-usage sample/miss composers
(``blizzard-mock:unit-test``). Pure, no store (``bzh:domain-takes-objects``).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.hub.runner_subscription_seed import (
    compose_subscription_miss,
    compose_subscription_sample,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_sample_lands_a_runner_external_usage_row_with_a_default_window() -> None:
    row = compose_subscription_sample(runner_id="r-1", slug="anthropic", name="Anthropic", sampled_at=_NOW)
    assert row.table == "runner_external_usage"
    assert row.values["runner_id"] == "r-1"
    assert row.values["slug"] == "anthropic"
    assert row.values["name"] == "Anthropic"
    assert row.values["sampled_at"] == _NOW
    assert row.values["updated_at"] == _NOW
    windows_raw = row.values["windows"]
    assert isinstance(windows_raw, str)
    windows = json.loads(windows_raw)
    assert windows and windows[0]["window"] and windows[0]["window_seconds"] > 0


def test_sample_honors_an_explicit_windows_override() -> None:
    row = compose_subscription_sample(runner_id="r-1", slug="anthropic", name="Anthropic", sampled_at=_NOW, windows=[])
    windows_raw = row.values["windows"]
    assert isinstance(windows_raw, str)
    assert json.loads(windows_raw) == []


def test_miss_lands_a_runner_external_usage_misses_row_carrying_the_reason() -> None:
    row = compose_subscription_miss(
        runner_id="r-1", slug="probe", name="Probe", missed_at=_NOW, reason="endpoint_unreachable"
    )
    assert row.table == "runner_external_usage_misses"
    assert row.values == {
        "runner_id": "r-1",
        "slug": "probe",
        "name": "Probe",
        "missed_at": _NOW,
        "reason": "endpoint_unreachable",
        "updated_at": _NOW,
    }
