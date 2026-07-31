"""Unit coverage for the question-trail composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_question`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.question_seed import QuestionCompositionError, QuestionSeed, compose_question

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _compose(
    *,
    options: Sequence[str] = (),
    answer: str | None = None,
    answered_by: str | None = None,
    delivered: bool = False,
) -> QuestionSeed:
    return compose_question(
        chunk_id="ch_1",
        clock=FixedClock(_NOW),
        rng=random.Random(1),
        question="q?",
        options=options,
        answer=answer,
        answered_by=answered_by,
        delivered=delivered,
    )


def test_open_question_lands_only_the_questions_row() -> None:
    seed = _compose()
    assert [row.table for row in seed.rows] == ["questions"]
    row = seed.rows[0]
    assert row.values["question_id"] == seed.question_id
    assert row.values["chunk_id"] == "ch_1"
    assert row.values["question"] == "q?"
    assert row.values["options"] == "[]"
    assert row.values["asked_at"] == _NOW


def test_options_are_json_encoded() -> None:
    seed = _compose(options=["a", "b"])
    assert seed.rows[0].values["options"] == '["a", "b"]'


def test_answer_lands_a_question_answers_row() -> None:
    seed = _compose(answer="left", answered_by="operator-1")
    assert [row.table for row in seed.rows] == ["questions", "question_answers"]
    answer_row = seed.rows[1]
    assert answer_row.values["question_id"] == seed.question_id
    assert answer_row.values["answer"] == "left"
    assert answer_row.values["answered_by"] == "operator-1"


def test_delivered_lands_an_answer_deliveries_row_too() -> None:
    seed = _compose(answer="left", answered_by="operator-1", delivered=True)
    assert [row.table for row in seed.rows] == ["questions", "question_answers", "answer_deliveries"]
    delivery_row = seed.rows[2]
    assert delivery_row.values["question_id"] == seed.question_id
    assert delivery_row.values["chunk_id"] == "ch_1"


def test_two_calls_mint_independent_non_colliding_question_ids() -> None:
    a = compose_question(chunk_id="ch_1", clock=FixedClock(_NOW), rng=random.Random(1), question="q1")
    b = compose_question(chunk_id="ch_1", clock=FixedClock(_NOW), rng=random.Random(2), question="q2")
    assert a.question_id != b.question_id


def test_answer_without_answered_by_is_refused() -> None:
    with pytest.raises(QuestionCompositionError, match="together"):
        _compose(answer="left")


def test_answered_by_without_answer_is_refused() -> None:
    with pytest.raises(QuestionCompositionError, match="together"):
        _compose(answered_by="operator-1")


def test_delivered_without_answer_is_refused() -> None:
    with pytest.raises(QuestionCompositionError, match="--delivered requires --answer"):
        _compose(delivered=True)
