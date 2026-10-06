"""In-memory hub state (``IHubState``) — process-local, forge-lifetime.

A fresh ``blizzard-mock-hub`` process starts empty; a scenario seeds it and tears it
down. No persistence, mirroring the forge's in-process metadata store.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState
from blizzard_mock.mock_hub.domain.state import (
    DeclaredSubscription,
    IHubState,
    ReportedRunnerFacts,
    RunnerCapability,
    RunnerRow,
    ScopeRow,
)


class InMemoryHubState:
    """Process-local chunk map + question map + runner registry. Implements ``IHubState``."""

    def __init__(self) -> None:
        self._chunks: dict[str, ChunkState] = {}
        self._runners: dict[str, RunnerRow] = {}
        #: Plaintext bearer token -> the runner it is the current token of. The real hub keeps a
        #: hash; a mock has nothing to protect.
        self._tokens: dict[str, str] = {}
        #: Revoked or rotated-away token -> the runner it was issued to.
        self._revoked_tokens: dict[str, str] = {}
        self._reported: dict[str, ReportedRunnerFacts] = {}
        self._questions: dict[str, QuestionState] = {}
        self._system_artifacts: dict[str, str] = {}
        self._scopes: dict[str, ScopeRow] = {}

    def put_chunk(self, chunk: ChunkState) -> None:
        self._chunks[chunk.chunk_id] = chunk

    def get_chunk(self, chunk_id: str) -> ChunkState | None:
        return self._chunks.get(chunk_id)

    def list_chunks(self) -> list[ChunkState]:
        return list(self._chunks.values())

    def add_runner(self, row: RunnerRow, *, token: str) -> None:
        if row.runner_id in self._runners:
            raise ValueError(f"runner {row.runner_id} is already added")
        self._runners[row.runner_id] = row
        self._tokens[token] = row.runner_id

    def record_registration(
        self,
        runner_id: str,
        *,
        name: str | None,
        workspace_id: str,
        at: datetime,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        env_capacity: int | None = None,
        capabilities: tuple[RunnerCapability, ...] = (),
        declared_subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
    ) -> bool:
        row = self._runners.get(runner_id)
        if row is None:
            raise LookupError(f"runner {runner_id} has not been added")
        first = row.registered_at is None
        if first:
            row.registered_at = at
        row.last_seen_at = at
        if name is not None:
            row.name = name
        row.workspace_id = workspace_id
        row.url = url
        row.redirect_uris = redirect_uris
        row.env_capacity = env_capacity
        row.capabilities = capabilities
        row.declared_subscriptions = declared_subscriptions
        row.gates = gates
        return first

    def runner_for_token(self, token: str) -> RunnerRow | None:
        runner_id = self._tokens.get(token)
        return self._runners.get(runner_id) if runner_id is not None else None

    def revoked_token_runner_id(self, token: str) -> str | None:
        return self._revoked_tokens.get(token)

    def replace_token(self, runner_id: str, *, token: str | None) -> None:
        for held in [t for t, owner in self._tokens.items() if owner == runner_id]:
            del self._tokens[held]
            self._revoked_tokens[held] = runner_id
        if token is not None:
            self._tokens[token] = runner_id

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

    def put_scope(self, row: ScopeRow) -> None:
        self._scopes[row.slug] = row

    def list_scopes(self) -> list[ScopeRow]:
        return sorted(self._scopes.values(), key=lambda row: row.created_at, reverse=True)

    def clear(self) -> None:
        self._chunks.clear()
        self._runners.clear()
        self._tokens.clear()
        self._revoked_tokens.clear()
        self._questions.clear()
        self._system_artifacts.clear()
        self._scopes.clear()


# Typecheck-time Protocol/adapter conformance sentinel (bzh:dependency-inversion) — pyright
# rejects the return if InMemoryHubState drifts from IHubState.
def _conforms_hub_state(x: InMemoryHubState) -> IHubState:
    return x
