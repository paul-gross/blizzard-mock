"""Unit coverage for the runner-store outbound-fact composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_outbound_fact`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.runner.outbound_fact_seed import QUESTION_ASKED, compose_outbound_fact

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_outbound_fact_lands_the_payload_as_json() -> None:
    row = compose_outbound_fact(
        kind=QUESTION_ASKED,
        chunk_id="ch_1",
        lease_id="lease_1",
        payload={"question_id": "qn_1"},
        created_at=_NOW,
    )
    assert row.table == "outbound_buffer"
    assert row.values["kind"] == QUESTION_ASKED
    assert row.values["payload"] == '{"question_id": "qn_1"}'


def test_compose_outbound_fact_defaults_to_pending() -> None:
    row = compose_outbound_fact(kind=QUESTION_ASKED, chunk_id="ch_1", lease_id="lease_1", payload={}, created_at=_NOW)
    assert row.values["acked_at"] is None


def test_compose_outbound_fact_lands_pre_acked_when_given() -> None:
    row = compose_outbound_fact(
        kind=QUESTION_ASKED, chunk_id="ch_1", lease_id="lease_1", payload={}, created_at=_NOW, acked_at=_NOW
    )
    assert row.values["acked_at"] == _NOW
