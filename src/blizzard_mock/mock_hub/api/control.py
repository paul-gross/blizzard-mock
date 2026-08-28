"""The mock hub's control plane — ``/_seed`` (state), ``/_levers`` (edge states), and
``/_captured`` (received-request capture, issue #86b).

Namespaced outside ``/api`` and exempt from the transport-edge levers, so a
test can always seed, arm/clear a lever, or read a capture.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard_mock.levers import Lever, LeverParams
from blizzard_mock.mock_hub.api.deps import AnswerControlBody, StopControlBody, get_captured, get_service
from blizzard_mock.mock_hub.domain.capture import ICaptureStore
from blizzard_mock.mock_hub.domain.levers import CATALOG, HubLever
from blizzard_mock.mock_hub.domain.models import ChunkSpec
from blizzard_mock.mock_hub.domain.service import ChunkNotFound, MockHubService, QuestionNotFound

seed_router = APIRouter(prefix="/_seed", tags=["control"])
levers_router = APIRouter(prefix="/_levers", tags=["control"])
captured_router = APIRouter(prefix="/_captured", tags=["control"])


@seed_router.post("/chunk", status_code=201)
def seed_chunk(spec: ChunkSpec, service: Annotated[MockHubService, Depends(get_service)]) -> dict[str, str]:
    chunk = service.seed_chunk(spec)
    return {"chunk_id": chunk.chunk_id, "graph_id": chunk.graph_id}


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
