"""Shared FastAPI dependencies and request bodies for the mock-hub routers.

The composition root stashes the wired ``MockHubService`` and the lever store on
``app.state``; routers reach them through these dependencies rather than constructing
collaborators (``bzh:dependency-injection``). Request bodies are permissive mirrors of
the ``blizzard.wire`` shapes the runner posts — only the fields the mock reads are named.
"""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, Field

from blizzard_mock.mock_hub.domain.capture import ICaptureStore
from blizzard_mock.mock_hub.domain.service import MockHubService


def get_service(request: Request) -> MockHubService:
    service: MockHubService = request.app.state.service
    return service


def get_captured(request: Request) -> ICaptureStore:
    captured: ICaptureStore = request.app.state.captured
    return captured


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
    # The runner's optional federation identity (issue #95) — named here (unlike most
    # of this module's "only the fields the mock reads" convention) because the mock
    # hub round-trips them into `MockHubService.register`, mirroring the real hub.
    url: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)


class RunnerFactBody(BaseModel):
    seq: int = 0
    kind: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class RunnerFactBatchBody(BaseModel):
    runner_id: str
    facts: list[RunnerFactBody] = Field(default_factory=list)


class PauseBody(BaseModel):
    paused: bool = True


class LeaseReportBody(BaseModel):
    """``POST /chunks/{id}/leases`` — mirrors ``blizzard.wire.facts.LeaseMintReport``."""

    epoch: int
    runner_id: str = "runner-mock"


class EscalationReportBody(BaseModel):
    """``POST /chunks/{id}/escalations`` — mirrors ``blizzard.wire.facts.EscalationReport``."""

    epoch: int
    runner_id: str = "runner-mock"
    takeover_command: str = ""
    wrapped_takeover_command: str = ""


class AnswerControlBody(BaseModel):
    """``POST /_seed/answer`` — test-control only; plays the operator's answer so a
    scenario can make the runner's poll return ``answered=True`` without a real
    operator surface (the fleet mirror carries no board-facing answer route)."""

    question_id: str
    answer: str
    answered_by: str = "operator"
