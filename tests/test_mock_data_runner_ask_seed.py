"""Unit coverage for the runner-store ask/park composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_ask_park`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``) that always returns the ``asks`` row paired with its
``park_facts`` sibling — never one without the other, never a ``park_resumes`` row.
"""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard_mock.mock_data.domain.runner.ask_seed import compose_ask_park

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_compose_ask_park_always_lands_both_rows() -> None:
    seeded = compose_ask_park(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which way?",
        session_id=None,
        asked_at=_NOW,
        parked_at=_NOW,
    )
    tables = [row.table for row in seeded.rows]
    assert tables == ["asks", "park_facts"]


def test_compose_ask_park_never_lands_a_park_resumes_row() -> None:
    seeded = compose_ask_park(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which way?",
        session_id=None,
        asked_at=_NOW,
        parked_at=_NOW,
    )
    assert "park_resumes" not in [row.table for row in seeded.rows]


def test_compose_ask_park_options_land_as_a_json_list() -> None:
    seeded = compose_ask_park(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which way?",
        options=["left", "right"],
        session_id=None,
        asked_at=_NOW,
        parked_at=_NOW,
    )
    ask_row = seeded.rows[0]
    assert ask_row.values["options"] == '["left", "right"]'


def test_compose_ask_park_shares_the_question_id_across_both_rows() -> None:
    seeded = compose_ask_park(
        lease_id="lease_1",
        chunk_id="ch_1",
        question_id="qn_1",
        question="which way?",
        session_id="sess_1",
        asked_at=_NOW,
        parked_at=_NOW,
    )
    ask_row, park_row = seeded.rows
    assert ask_row.values["question_id"] == "qn_1"
    assert park_row.values["question_id"] == "qn_1"
    assert ask_row.values["session_id"] == "sess_1"
