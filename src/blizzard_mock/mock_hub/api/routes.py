"""The hub-mirror routes — the ``/api`` surface a runner consumes (D-012).

Vendor-native paths and JSON identical to the real hub OpenAPI subset the reconciliation
loop calls: queue peek, route claim, chunk detail, envelope re-read, completion + decision
apply, the fact-intake push, and the runner registry. Controllers hold only the
``MockHubService`` (``bzh:controller-read-only``); every rule lives in the service.

The ``drop_ack`` lever is realised *here* on the completions route: the service advances
the real transition, then — the ack being "dropped" — the route answers 503, so the
runner's re-flush lands on the now-idempotent state (D-090).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard_mock.mock_hub.api.deps import (
    CompletionBody,
    DecisionBody,
    RouteClaimBody,
    RunnerFactBatchBody,
    RunnerRegistrationBody,
    get_service,
)
from blizzard_mock.mock_hub.domain.service import ChunkNotFound, ClaimConflict, MockHubService

router = APIRouter(prefix="/api", tags=["hub"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, bool]:
    return {"ready": True}


@router.get("/queue/peek")
def peek_queue(service: Annotated[MockHubService, Depends(get_service)]) -> object:
    return service.peek()


@router.post("/routes", status_code=201)
def claim_route(body: RouteClaimBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.claim(
            body.chunk_id,
            runner_id=body.runner_id,
            workspace_id=body.workspace_id,
            environment_ids=body.environment_ids,
        )
    except ClaimConflict as exc:
        return JSONResponse(
            status_code=409,
            content={
                "chunk_id": body.chunk_id,
                "held_by_runner_id": exc.held_by_runner_id,
                "detail": "chunk already claimed",
            },
        )
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.chunk_detail(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@router.get("/chunks/{chunk_id}/envelope")
def get_envelope(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.envelope(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@router.post("/chunks/{chunk_id}/completions")
def submit_completion(
    chunk_id: str, body: CompletionBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    try:
        response = service.apply_completion(
            chunk_id, epoch=body.epoch, from_node_id=body.from_node_id, choice=body.choice
        )
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    if service.pop_drop_ack(chunk_id):
        # The transition landed; the ack is dropped (503) — the runner's re-flush is idempotent.
        return JSONResponse(status_code=503, content={"detail": "ack dropped after apply"})
    return response


@router.post("/chunks/{chunk_id}/decisions")
def submit_decision(
    chunk_id: str, body: DecisionBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    try:
        return service.apply_decision(chunk_id, epoch=body.epoch, from_node_id=body.from_node_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@router.post("/events")
def push_facts(body: RunnerFactBatchBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    return service.ingest_facts(body.runner_id, [f.model_dump() for f in body.facts])


@router.post("/runners", status_code=201)
def register_runner(body: RunnerRegistrationBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    first = service.register(body.runner_id, body.workspace_id)
    return {"runner_id": body.runner_id, "first_registration": first}


@router.get("/runners/{runner_id}")
def get_runner(runner_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    view = service.runner_view(runner_id)
    if view is None:
        return JSONResponse(status_code=404, content={"detail": f"unknown runner {runner_id}"})
    return view
