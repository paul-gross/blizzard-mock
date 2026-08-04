"""Composes one question's ``FactRow`` set — the ``questions``/``question_answers``/
``answer_deliveries`` trail (``blizzard/hub/store/schema.py``, the ask/answer
rendezvous, ``bzh:facts-not-status``).

A question is open exactly while no ``question_answers`` row exists for it (the
primary key IS the question id — first-write-wins CAS); an answer is *delivered* once
``answer_deliveries`` carries a row (board-detail only, does not affect derived
status). The real schema has no dedicated "resumed" row beyond that delivery, so
``--resumed`` (``cli.py``) is a pure marker requiring ``--delivered``, not a fact this
module composes.

Each call mints its own ``qn_<ulid>`` question id, so two calls against the same chunk
land two independent, non-colliding trails — the multi-question-per-chunk shape a UI
trail test needs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from random import Random

from blizzard_mock.clock import Clock
from blizzard_mock.mock_data.domain import ids
from blizzard_mock.mock_data.domain.facts import FactRow


class QuestionCompositionError(Exception):
    """An ``--answer``/``--answered-by``/``--delivered`` combination ``compose_question`` cannot honor."""


@dataclass(frozen=True)
class QuestionSeed:
    """One composed question: its minted id and the exact ``FactRow``\\ s to write."""

    question_id: str
    rows: list[FactRow] = field(default_factory=list)


def compose_question(
    *,
    chunk_id: str,
    clock: Clock,
    rng: Random,
    question: str,
    options: Sequence[str] = (),
    node_id: str | None = None,
    session_id: str | None = None,
    runner_id: str = "runner-seed",
    epoch: int = 1,
    answer: str | None = None,
    answered_by: str | None = None,
    delivered: bool = False,
) -> QuestionSeed:
    """Compose one question, optionally already answered and/or delivered.

    ``answer``/``answered_by`` must both be given or both omitted — an answer with no
    author (or vice versa) is not a fact the real schema can hold.
    ``delivered=True`` requires an answer already given — a delivery is the resume
    executed *around* an answer, so one cannot exist without the other. Raises
    :class:`QuestionCompositionError` for either violation.
    """
    if (answer is None) != (answered_by is None):
        raise QuestionCompositionError("--answer and --answered-by must be supplied together, or neither")
    if delivered and answer is None:
        raise QuestionCompositionError("--delivered requires --answer (a delivery implies an answer exists)")

    minted_question_id = ids.mint(ids.QUESTION_PREFIX, clock, rng)
    now = clock.now()
    rows: list[FactRow] = [
        FactRow(
            table="questions",
            values={
                "question_id": minted_question_id,
                "chunk_id": chunk_id,
                "node_id": node_id,
                "session_id": session_id,
                "runner_id": runner_id,
                "epoch": epoch,
                "question": question,
                "options": json.dumps(list(options)),
                "asked_at": now,
            },
        )
    ]
    if answer is not None:
        rows.append(
            FactRow(
                table="question_answers",
                values={
                    "question_id": minted_question_id,
                    "answer": answer,
                    "answered_by": answered_by,
                    "answered_at": now + timedelta(seconds=1),
                },
            )
        )
    if delivered:
        rows.append(
            FactRow(
                table="answer_deliveries",
                values={
                    "question_id": minted_question_id,
                    "chunk_id": chunk_id,
                    "delivered_at": now + timedelta(seconds=2),
                },
            )
        )
    return QuestionSeed(question_id=minted_question_id, rows=rows)
