"""The mock runner's control surface — its own HTTP API.

Two families: the runner's thin **served surface** (``/api/health``, ``/api/ready``) that
mirrors the real runner, and the **drive** plane (``/_drive/*``) a test POSTs to so the
driver performs a runner-role call against the hub and reports what it observed. The
``/_levers`` plane arms the runner-side distortions. Controllers hold only the
``MockRunnerService`` (``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from blizzard_mock.levers import Lever, LeverParams
from blizzard_mock.mock_runner.domain.levers import CATALOG, RunnerLever
from blizzard_mock.mock_runner.domain.models import (
    AskBody,
    ChunkQueryBody,
    ClaimBody,
    CompleteBody,
    DecideBody,
    DeclareGitCommitBody,
    EscalateBody,
    GitCommitDeclarationBody,
    LeaseQueryBody,
    PauseBody,
    PollAnswerBody,
    ReportEventBody,
    ResumeBody,
)
from blizzard_mock.mock_runner.domain.service import MockRunnerService

api_router = APIRouter(prefix="/api", tags=["runner"])
drive_router = APIRouter(prefix="/_drive", tags=["control"])
levers_router = APIRouter(prefix="/_levers", tags=["control"])


def get_service(request: Request) -> MockRunnerService:
    service: MockRunnerService = request.app.state.service
    return service


@api_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@api_router.get("/ready")
def ready() -> dict[str, bool]:
    return {"ready": True}


@api_router.post("/leases/{lease_id}/git-commits")
def record_git_commit_declaration(
    lease_id: str, body: GitCommitDeclarationBody, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    """The mock's served counterpart to the real runner's ``POST /api/leases/{lease_id}/
    git-commits`` (issue #143, Phase 3) — no lease-token auth here (the mock runner holds
    no lease-token store), a lease-scoped local write only, exactly wire-shape-compatible
    with the real route."""
    return service.declare_git_commit(
        lease_id, forge=body.forge, repo=body.repo, branch=body.branch, commit=body.commit
    )


@drive_router.post("/register")
def drive_register(service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.register()


@drive_router.post("/peek")
def drive_peek(service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.peek()


@drive_router.post("/claim")
def drive_claim(body: ClaimBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.claim(body.chunk_id, body.environment_ids)


@drive_router.post("/complete")
def drive_complete(body: CompleteBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.complete(body.chunk_id, body.choice, body.artifacts, body.check_results)


@drive_router.post("/get-chunk")
def drive_get_chunk(
    body: ChunkQueryBody, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    return service.get_chunk(body.chunk_id)


@drive_router.post("/declare-git-commit")
def drive_declare_git_commit(
    body: DeclareGitCommitBody, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    """Drive a git-commit declaration directly against the mock's local store (issue #143,
    Phase 3) — the produces-kind analogue of ``CompleteBody.artifacts``, so a service test
    can set declaration state without a raw client to the served lease-scoped route."""
    return service.declare_git_commit(
        body.lease_id, forge=body.forge, repo=body.repo, branch=body.branch, commit=body.commit
    )


@drive_router.post("/get-git-commits")
def drive_get_git_commits(
    body: LeaseQueryBody, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    return {"declarations": service.git_commits_for_lease(body.lease_id)}


@drive_router.post("/reset")
def drive_reset(service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, bool]:
    service.reset()
    return {"reset": True}


@drive_router.post("/escalate")
def drive_escalate(body: EscalateBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.escalate(body.chunk_id, body.takeover_command)


@drive_router.post("/decide")
def drive_decide(body: DecideBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.decide(body.chunk_id, body.choice)


@drive_router.post("/ask")
def drive_ask(body: AskBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.ask(body.chunk_id, body.question, body.options)


@drive_router.post("/report-event")
def drive_report_event(
    body: ReportEventBody, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    return service.report_event(
        severity=body.severity,
        kind=body.kind,
        message=body.message,
        chunk_id=body.chunk_id,
        lease_id=body.lease_id,
        node_name=body.node_name,
        detail=body.detail,
    )


@drive_router.post("/poll-answer")
def drive_poll_answer(
    body: PollAnswerBody, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    return service.poll_answer(body.question_id)


@drive_router.post("/pause")
def drive_pause(body: PauseBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.pause(body.by, body.reason)


@drive_router.post("/resume")
def drive_resume(body: ResumeBody, service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return service.resume(body.by)


@levers_router.get("")
def list_levers(service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, Any]:
    return {
        "catalog": CATALOG,
        "levers": sorted(k.value for k in RunnerLever),
        "active": [lever.model_dump() for lever in service.levers.active()],
    }


@levers_router.post("/reset")
def reset_levers(service: Annotated[MockRunnerService, Depends(get_service)]) -> dict[str, bool]:
    service.levers.clear_all()
    return {"cleared": True}


@levers_router.post("/{kind}")
def arm_lever(
    kind: str, params: LeverParams, service: Annotated[MockRunnerService, Depends(get_service)]
) -> dict[str, Any]:
    valid = {k.value for k in RunnerLever}
    if kind not in valid:
        return {"error": f"unknown lever {kind!r}", "levers": sorted(valid)}
    lever = Lever(kind=kind, chunk_id=params.chunk_id, remaining=params.remaining, payload=params.payload)
    service.levers.arm(lever)
    return {"armed": lever.model_dump()}


@levers_router.delete("/{kind}")
def clear_lever(
    kind: str, service: Annotated[MockRunnerService, Depends(get_service)], chunk_id: str | None = None
) -> dict[str, Any]:
    service.levers.clear(kind, chunk_id)
    return {"cleared": kind, "chunk_id": chunk_id}
