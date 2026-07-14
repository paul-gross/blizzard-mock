"""Shared FastAPI dependencies and request bodies for the mock-hub routers.

The composition root stashes the wired ``MockHubService`` and the lever store on
``app.state``; routers reach them through these dependencies rather than constructing
collaborators (``bzh:dependency-injection``). Request bodies are permissive mirrors of
the ``blizzard.wire`` shapes the runner posts — only the fields the mock reads are named.
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, Field

from blizzard_mock.mock_hub.domain.service import MockHubService


def get_service(request: Request) -> MockHubService:
    service: MockHubService = request.app.state.service
    return service


class RouteClaimBody(BaseModel):
    chunk_id: str
    runner_id: str
    workspace_id: str = "workspace-mock"
    environment_ids: list[str] = Field(default_factory=list)


class CompletionBody(BaseModel):
    choice: str
    epoch: int
    runner_id: str = "runner-mock"
    from_node_id: str
    check_results: list[dict[str, object]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class DecisionBody(BaseModel):
    from_node_id: str
    epoch: int
    runner_id: str = "runner-mock"
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class RunnerRegistrationBody(BaseModel):
    runner_id: str
    workspace_id: str = "workspace-mock"


class RunnerFactBody(BaseModel):
    seq: int = 0
    kind: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class RunnerFactBatchBody(BaseModel):
    runner_id: str
    facts: list[RunnerFactBody] = Field(default_factory=list)


class PauseBody(BaseModel):
    paused: bool = True
