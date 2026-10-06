"""The mock hub's control plane — ``/_seed`` (state), ``/_levers`` (edge states), and
``/_captured`` (received-request capture).

Namespaced outside ``/api`` and exempt from the transport-edge levers, so a
test can always seed, arm/clear a lever, or read a capture.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard_mock.levers import Lever, LeverParams
from blizzard_mock.mock_hub.api.deps import (
    AnswerControlBody,
    RunnerRetireControlBody,
    RunnerSeedControlBody,
    StopControlBody,
    get_captured,
    get_service,
)
from blizzard_mock.mock_hub.domain.capture import ICaptureStore
from blizzard_mock.mock_hub.domain.levers import CATALOG, HubLever
from blizzard_mock.mock_hub.domain.models import ChunkSpec, ScopeSpec, SystemArtifactSpec
from blizzard_mock.mock_hub.domain.service import ChunkNotFound, MockHubService, QuestionNotFound, UnknownRunner

seed_router = APIRouter(prefix="/_seed", tags=["control"])
levers_router = APIRouter(prefix="/_levers", tags=["control"])
captured_router = APIRouter(prefix="/_captured", tags=["control"])


@seed_router.post("/chunk", status_code=201)
def seed_chunk(spec: ChunkSpec, service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, str]:
    chunk = service.seed_chunk(spec)
    return {"chunk_id": chunk.chunk_id, "graph_id": chunk.graph_id}


@seed_router.post("/system-artifacts", status_code=201)
def seed_system_artifact(
    spec: SystemArtifactSpec, service: Annotated[MockHubService, Depends(get_service)]
) -> dict[str, str]:
    """Publish (or replace) one ``ArtifactScope.SYSTEM`` document — global, so unlike
    ``/_seed/chunk`` this names no chunk to seed it onto."""
    service.seed_system_artifact(spec)
    return {"name": spec.name}


@seed_router.post("/scopes", status_code=201)
def seed_scope(spec: ScopeSpec, service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, str]:
    """Upsert one scope in the deployment's vocabulary — global, unlike ``/_seed/chunk``."""
    service.seed_scope(spec)
    return {"slug": spec.slug}


@seed_router.post("/reset")
def reset(service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, bool]:
    service.reset()
    return {"reset": True}


@seed_router.post("/answer")
def seed_answer(body: AnswerControlBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Test-control only — plays the operator's answer so a scenario can make the
    runner's ``GET /questions/{id}`` poll return ``answered=True`` without a real
    operator surface (the fleet mirror carries no board-facing answer route)."""
    try:
        service.answer_question(body.question_id, answer=body.answer, answered_by=body.answered_by)
    except QuestionNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    return {"answered": True, "question_id": body.question_id}


@seed_router.post("/stop")
def seed_stop(body: StopControlBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Test-control only — plays the operator's stop verb so a scenario can drive a
    seeded chunk to ``stopped`` without a real operator surface (the fleet mirror
    carries no board-facing stop route)."""
    try:
        service.stop_chunk(body.chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    return {"stopped": True, "chunk_id": body.chunk_id}


@seed_router.post("/runners", status_code=201)
def seed_runner(body: RunnerSeedControlBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Test-control only — adds a runner never connected, exactly as ``POST /api/runners``
    does, except that a scenario may pin its id and bearer token. A pinned id already added
    is refused ``409``."""
    try:
        return service.add_runner(name=body.name, runner_id=body.runner_id, token=body.token)
    except ValueError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@seed_router.post("/runners/{runner_id}/retire")
def seed_retire_runner(
    runner_id: str, body: RunnerRetireControlBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """Test-control only — plays the operator's retire verb: the runner is retired and its token
    revoked, so the identity route answers that token ``retired``."""
    try:
        service.retire(runner_id, by=body.by)
    except UnknownRunner as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    return {"retired": True, "runner_id": runner_id}


@seed_router.post("/runners/{runner_id}/token-revocations")
def seed_revoke_runner_token(runner_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Test-control only — plays the operator's revoke-token verb: the runner stays added, and its
    token answers ``revoked`` until a rotation issues another."""
    try:
        service.revoke_token(runner_id)
    except UnknownRunner as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    return {"revoked": True, "runner_id": runner_id}


@seed_router.post("/runners/{runner_id}/pause")
def seed_pause_runner(
    runner_id: str, service: Annotated[MockHubService, Depends(get_service)], paused: bool = True
) -> object:
    """Test-control only — sets the fleet's brake on the runner (``?paused=false`` releases it), so a
    claim from it is refused as paused."""
    try:
        service.set_paused(runner_id, paused=paused)
    except UnknownRunner as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    return {"paused": paused, "runner_id": runner_id}


@levers_router.get("")
def list_levers(service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, Any]:
    return {
        "catalog": CATALOG,
        "levers": sorted(k.value for k in HubLever),
        "active": [lever.model_dump() for lever in service.levers.active()],
    }


@levers_router.post("/reset")
def reset_levers(service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, bool]:
    service.levers.clear_all()
    return {"cleared": True}


@levers_router.post("/{kind}")
def arm_lever(
    kind: str, params: LeverParams, service: Annotated[MockHubService, Depends(get_service)]
) -> dict[str, Any]:
    valid = {k.value for k in HubLever}
    if kind not in valid:
        return {"error": f"unknown lever {kind!r}", "levers": sorted(valid)}
    lever = Lever(kind=kind, chunk_id=params.chunk_id, remaining=params.remaining, payload=params.payload)
    service.levers.arm(lever)
    return {"armed": lever.model_dump()}


@levers_router.delete("/{kind}")
def clear_lever(
    kind: str, service: Annotated[MockHubService, Depends(get_service)], chunk_id: str | None = None
) -> dict[str, Any]:
    service.levers.clear(kind, chunk_id)
    return {"cleared": kind, "chunk_id": chunk_id}


@captured_router.get("")
def list_captured(captured: Annotated[ICaptureStore, Depends(get_captured)]) -> dict[str, Any]:
    return {"requests": captured.all()}


@captured_router.post("/reset")
def reset_captured(captured: Annotated[ICaptureStore, Depends(get_captured)]) -> dict[str, bool]:
    captured.clear()
    return {"cleared": True}
