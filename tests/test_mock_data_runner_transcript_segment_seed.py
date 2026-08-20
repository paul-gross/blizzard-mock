"""Unit coverage for the transcript-segment composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_transcript_segment`` is a plain function over already-loaded
data (``bzh:domain-takes-objects``) — runner-only, no hub counterpart.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.ids import seeded_rng
from blizzard_mock.mock_data.domain.runner.transcript_segment_seed import compose_transcript_segment

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_transcript_segment_mints_a_prefixed_id() -> None:
    row = compose_transcript_segment(
        chunk_id="ch_1",
        node_id="build",
        epoch=1,
        generation=1,
        lease_id="lease_1",
        session_id="sess_1",
        normalizer_version="mock-1",
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
        stamped_at=_NOW,
    )
    assert row.table == "transcript_segments"
    assert str(row.values["segment_id"]).startswith("seg_")


def test_compose_transcript_segment_defaults_to_open() -> None:
    row = compose_transcript_segment(
        chunk_id="ch_1",
        node_id="build",
        epoch=1,
        generation=1,
        lease_id="lease_1",
        session_id="sess_1",
        normalizer_version="mock-1",
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
        stamped_at=_NOW,
    )
    assert row.values["finalized_at"] is None
    assert row.values["shipped_bytes"] == 0
    assert row.values["shipped_turns"] == 0
    assert row.values["cursor"] is None


def test_compose_transcript_segment_finalized_stamps_the_close() -> None:
    row = compose_transcript_segment(
        chunk_id="ch_1",
        node_id="build",
        epoch=1,
        generation=1,
        lease_id="lease_1",
        session_id="sess_1",
        normalizer_version="mock-1",
        clock=FixedClock(_NOW),
        rng=seeded_rng(1),
        stamped_at=_NOW,
        finalized_at=_NOW,
    )
    assert row.values["finalized_at"] == _NOW
