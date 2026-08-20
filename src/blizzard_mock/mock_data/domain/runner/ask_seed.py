"""Composes a runner-store ask, parked — the ``asks`` row plus its ``park_facts``
sibling, together, and no ``park_resumes`` (an open park).

The daemon reads a park as open only with both rows present, so this module never
composes one without the other.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from blizzard_mock.mock_data.domain.facts import FactRow


@dataclass(frozen=True)
class RunnerAskSeed:
    """One composed ask-and-park: the two ``FactRow``\\ s to write."""

    rows: list[FactRow] = field(default_factory=list)


def compose_ask_park(
    *,
    lease_id: str,
    chunk_id: str,
    question_id: str,
    question: str,
    options: Sequence[str] = (),
    session_id: str | None,
    asked_at: datetime,
    parked_at: datetime,
) -> RunnerAskSeed:
    """One open ask, parked on it — no ``park_resumes`` composed."""
    rows = [
        FactRow(
            table="asks",
            values={
                "lease_id": lease_id,
                "chunk_id": chunk_id,
                "question_id": question_id,
                "question": question,
                "options": json.dumps(list(options)),
                "session_id": session_id,
                "asked_at": asked_at,
            },
        ),
        FactRow(
            table="park_facts",
            values={"lease_id": lease_id, "chunk_id": chunk_id, "question_id": question_id, "parked_at": parked_at},
        ),
    ]
    return RunnerAskSeed(rows=rows)
