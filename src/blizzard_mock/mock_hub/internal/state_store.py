"""In-memory hub state (``IHubState``) — process-local, forge-lifetime.

A fresh ``blizzard-mock-hub`` process starts empty; a scenario seeds it and tears it
down. No persistence, mirroring the forge's in-process metadata store.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState
from blizzard_mock.mock_hub.domain.state import ReportedRunnerFacts, RunnerRow


class InMemoryHubState:
    """Process-local chunk map + question map + runner registry. Implements ``IHubState``."""

    def __init__(self) -> None:
        self._chunks: dict[str, ChunkState] = {}
        self._runners: dict[str, RunnerRow] = {}
        self._reported: dict[str, ReportedRunnerFacts] = {}
        self._questions: dict[str, QuestionState] = {}
        self._system_artifacts: dict[str, str] = {}

    def put_chunk(self, chunk: ChunkState) -> None:
        self._chunks[chunk.chunk_id] = chunk

    def get_chunk(self, chunk_id: str) -> ChunkState | None:
        return self._chunks.get(chunk_id)

    def list_chunks(self) -> list[ChunkState]:
        return list(self._chunks.values())

    def upsert_runner(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        at: datetime,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        env_capacity: int | None = None,
    ) -> bool:
        existing = self._runners.get(runner_id)
        if existing is None:
            self._runners[runner_id] = RunnerRow(
                runner_id,
                workspace_id=workspace_id,
                at=at,
                url=url,
                redirect_uris=redirect_uris,
                env_capacity=env_capacity,
            )
            return True
        existing.last_seen_at = at
        existing.workspace_id = workspace_id
        existing.url = url
        existing.redirect_uris = redirect_uris
        existing.env_capacity = env_capacity
        return False

    def reported_facts(self, runner_id: str) -> ReportedRunnerFacts:
        return self._reported.setdefault(runner_id, ReportedRunnerFacts())

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

    def put_system_artifact(self, name: str, *, content: str) -> None:
        self._system_artifacts[name] = content

    def get_system_artifact(self, name: str) -> str | None:
        return self._system_artifacts.get(name)

    def list_system_artifacts(self) -> list[tuple[str, str]]:
        return sorted(self._system_artifacts.items())

    def clear(self) -> None:
        self._chunks.clear()
        self._runners.clear()
        self._questions.clear()
        self._system_artifacts.clear()
