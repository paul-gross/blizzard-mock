"""Unit coverage for the event-log composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_event`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard_mock.mock_data.domain.event_seed import EventCompositionError, compose_event

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_event_lands_the_supplied_fields() -> None:
    row = compose_event(
        kind="chunk.noticed",
        severity="warning",
        message="hi",
        runner_id="r-1",
        chunk_id="ch_1",
        lease_id="lease_1",
        node_name="build",
        detail='{"a": 1}',
        recorded_at=_NOW,
    )
    assert row.table == "event_log"
    assert row.values == {
        "recorded_at": _NOW,
        "severity": "warning",
        "kind": "chunk.noticed",
        "runner_id": "r-1",
        "chunk_id": "ch_1",
        "lease_id": "lease_1",
        "node_name": "build",
        "message": "hi",
        "detail": '{"a": 1}',
    }


def test_chunk_id_and_detail_default_to_none() -> None:
    row = compose_event(kind="runner.registered", severity="info", message="hi", runner_id="r-1", recorded_at=_NOW)
    assert row.values["chunk_id"] is None
    assert row.values["detail"] is None


def test_unknown_severity_is_refused() -> None:
    with pytest.raises(EventCompositionError, match="unknown severity"):
        compose_event(kind="k", severity="bogus", message="m", runner_id="r-1", recorded_at=_NOW)


def test_invalid_json_detail_is_refused() -> None:
    with pytest.raises(EventCompositionError, match="not valid JSON"):
        compose_event(kind="k", severity="info", message="m", runner_id="r-1", detail="{not json", recorded_at=_NOW)


def test_detail_contents_are_never_interpreted_only_validated() -> None:
    row = compose_event(kind="k", severity="info", message="m", runner_id="r-1", detail="[1, 2, 3]", recorded_at=_NOW)
    assert row.values["detail"] == "[1, 2, 3]"
