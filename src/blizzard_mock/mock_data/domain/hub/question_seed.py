"""Composes one question's ``FactRow`` set — the ``questions``/``question_answers``/
``answer_deliveries`` trail (``bzh:facts-not-status``).

A question is open while no ``question_answers`` row exists for it; it is
delivered once ``answer_deliveries`` carries a row.
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

    ``answer``/``answered_by`` must both be given or both omitted;
    ``delivered=True`` requires an answer already given.
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
