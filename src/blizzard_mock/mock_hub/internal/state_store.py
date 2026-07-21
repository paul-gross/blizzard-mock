"""In-memory hub state (``IHubState``) — process-local, forge-lifetime.

A fresh ``blizzard-mock-hub`` process starts empty; a scenario seeds it and tears it
down. No persistence, mirroring the forge's in-process metadata store.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState
from blizzard_mock.mock_hub.domain.state import RunnerRow


class InMemoryHubState:
    """Process-local chunk map + question map + runner registry. Implements ``IHubState``."""

    def __init__(self) -> None:
        self._chunks: dict[str, ChunkState] = {}
        self._runners: dict[str, RunnerRow] = {}
        self._questions: dict[str, QuestionState] = {}

    def put_chunk(self, chunk: ChunkState) -> None:
        self._chunks[chunk.chunk_id] = chunk

    def get_chunk(self, chunk_id: str) -> ChunkState | None:
        return self._chunks.get(chunk_id)

    def list_chunks(self) -> list[ChunkState]:
        return list(self._chunks.values())

    def upsert_runner(self, runner_id: str, workspace_id: str, at: datetime) -> bool:
        existing = self._runners.get(runner_id)
        if existing is None:
            self._runners[runner_id] = RunnerRow(runner_id, workspace_id, at)
            return True
        existing.last_seen_at = at
        existing.workspace_id = workspace_id
        return False

    def get_runner(self, runner_id: str) -> RunnerRow | None:
        return self._runners.get(runner_id)

    def list_runners(self) -> list[RunnerRow]:
        return list(self._runners.values())

    def put_question(self, question: QuestionState) -> None:
        self._questions[question.question_id] = question

    def get_question(self, question_id: str) -> QuestionState | None:
        return self._questions.get(question_id)

    def list_questions(self) -> list[QuestionState]:
        return list(self._questions.values())

    def clear(self) -> None:
        self._chunks.clear()
        self._runners.clear()
        self._questions.clear()
