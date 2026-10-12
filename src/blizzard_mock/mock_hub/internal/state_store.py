"""In-memory hub state (``IHubState``) — process-local, forge-lifetime.

A fresh ``blizzard-mock-hub`` process starts empty; a scenario seeds it and tears it
down. No persistence, mirroring the forge's in-process metadata store.
"""

from __future__ import annotations

from datetime import datetime

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState
from blizzard_mock.mock_hub.domain.state import (
    DeclaredSubscription,
    HostRow,
    IHubState,
    ProjectRow,
    ReportedRunnerFacts,
    RunnerCapability,
    RunnerRow,
    ScopeRow,
    ServedEntry,
    TenantRow,
)


class InMemoryHubState:
    """Process-local chunk map + question map + tenant, project and runner registries. Implements ``IHubState``."""

    def __init__(self) -> None:
        self._tenants: dict[str, TenantRow] = {}
        #: Insertion-ordered, so a tenant's projects list in the order they were created.
        self._projects: dict[str, ProjectRow] = {}
        self._chunks: dict[str, ChunkState] = {}
        self._runners: dict[str, RunnerRow] = {}
        #: Plaintext bearer token -> the runner it is the current token of. The real hub keeps a
        #: hash; a mock has nothing to protect.
        self._tokens: dict[str, str] = {}
        #: Revoked or rotated-away token -> the runner it was issued to.
        self._revoked_tokens: dict[str, str] = {}
        self._hosts: dict[str, HostRow] = {}
        #: Plaintext host bearer token -> the host it is the current token of; as for runners.
        self._host_tokens: dict[str, str] = {}
        #: Revoked or rotated-away host token -> the host it was issued to.
        self._revoked_host_tokens: dict[str, str] = {}
        self._reported: dict[str, ReportedRunnerFacts] = {}
        self._questions: dict[str, QuestionState] = {}
        self._system_artifacts: dict[str, str] = {}
        self._scopes: dict[str, ScopeRow] = {}

    def put_tenant(self, row: TenantRow) -> None:
        self._tenants[row.tenant_id] = row

    def get_tenant(self, tenant_id: str) -> TenantRow | None:
        return self._tenants.get(tenant_id)

    def list_tenants(self) -> list[TenantRow]:
        return list(self._tenants.values())

    def put_project(self, row: ProjectRow) -> None:
        self._projects[row.project_id] = row

    def get_project(self, project_id: str) -> ProjectRow | None:
        return self._projects.get(project_id)

    def list_projects(self) -> list[ProjectRow]:
        return list(self._projects.values())

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
        projects: tuple[ServedEntry, ...] | None = None,
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
        if projects is not None:
            row.projects = projects
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

    def add_host(self, row: HostRow, *, token: str) -> None:
        if row.host_id in self._hosts:
            raise ValueError(f"host {row.host_id} is already added")
        self._hosts[row.host_id] = row
        self._host_tokens[token] = row.host_id

    def get_host(self, host_id: str) -> HostRow | None:
        return self._hosts.get(host_id)

    def list_hosts(self) -> list[HostRow]:
        return list(self._hosts.values())

    def host_for_token(self, token: str) -> HostRow | None:
        host_id = self._host_tokens.get(token)
        return self._hosts.get(host_id) if host_id is not None else None

    def revoked_token_host_id(self, token: str) -> str | None:
        return self._revoked_host_tokens.get(token)

    def replace_host_token(self, host_id: str, *, token: str | None) -> None:
        for held in [t for t, owner in self._host_tokens.items() if owner == host_id]:
            del self._host_tokens[held]
            self._revoked_host_tokens[held] = host_id
        if token is not None:
            self._host_tokens[token] = host_id

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
        self._tenants.clear()
        self._projects.clear()
        self._chunks.clear()
        self._runners.clear()
        self._tokens.clear()
        self._revoked_tokens.clear()
        self._hosts.clear()
        self._host_tokens.clear()
        self._revoked_host_tokens.clear()
        self._questions.clear()
        self._system_artifacts.clear()
        self._scopes.clear()


# Typecheck-time Protocol/adapter conformance sentinel (bzh:dependency-inversion) — pyright
# rejects the return if InMemoryHubState drifts from IHubState.
def _conforms_hub_state(x: InMemoryHubState) -> IHubState:
    return x
