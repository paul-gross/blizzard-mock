"""The hub-mirror routes — the ``/api`` surface a runner consumes (D-012).

Vendor-native paths and JSON matching the real hub's OpenAPI subset.
Controllers hold only the ``MockHubService`` (``bzh:controller-read-only``).
``router`` is unauthenticated liveness; ``fleet_router`` is everything else.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from blizzard_mock.mock_hub.api.deps import (
    CompletionBody,
    DecisionBody,
    EscalationReportBody,
    LeaseReportBody,
    RouteClaimBody,
    RunnerFactBatchBody,
    RunnerRegistrationBody,
    TranscriptSegmentBatchBody,
    get_service,
)
from blizzard_mock.mock_hub.domain.service import ChunkNotFound, ClaimConflict, MockHubService, QuestionNotFound

#: Unauthenticated liveness — unaffected by the fleet partition, exactly as on the real
#: hub.
router = APIRouter(prefix="/api", tags=["hub"])

#: The runner-facing hub mirror (issue #87) — every route below is
#: runner-originating traffic, moved under ``/api/fleet`` as a block.
fleet_router = APIRouter(prefix="/api/fleet", tags=["hub"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, bool]:
    return {"ready": True}


@fleet_router.get("/queue/peek")
def peek_queue(service: Annotated[MockHubService, Depends(get_service)]) -> object:
    return service.peek()


@fleet_router.post("/routes", status_code=201)
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


@fleet_router.post("/chunks/{chunk_id}/route-token")
def rekey_route_token(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.rekey_route_token(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.chunk_detail(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/envelope")
def get_envelope(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.envelope(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/work-items")
# Both the route and its deprecated `/pm-items` alias are served (issue #55),
# pinned by tests/test_pin_mock.py.
@fleet_router.get("/chunks/{chunk_id}/pm-items")
def get_work_items(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.work_items(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.post("/chunks/{chunk_id}/completions")
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


@fleet_router.post("/chunks/{chunk_id}/decisions")
def submit_decision(
    chunk_id: str, body: DecisionBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    try:
        return service.apply_decision(chunk_id, epoch=body.epoch, from_node_id=body.from_node_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.post("/events")
def push_facts(body: RunnerFactBatchBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    return service.ingest_facts(body.runner_id, [f.model_dump() for f in body.facts])


@fleet_router.post("/transcripts")
def push_transcripts(
    body: TranscriptSegmentBatchBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """The transcript lane's own push (blizzard#247) — distinct from :func:`push_facts`,
    mirroring the real hub's ``POST /api/fleet/transcripts``."""
    return service.ingest_transcripts(body.runner_id, [r.model_dump() for r in body.records])


@fleet_router.get("/chunks/{chunk_id}/transcript-segments")
def get_lease_transcript_segments(
    chunk_id: str, node_id: str, epoch: int, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """A lease's retained transcript (blizzard#249) — mirrors the real hub's runner-scoped
    ``GET /api/fleet/chunks/{chunk_id}/transcript-segments``; the mock enforces no
    bearer-token confinement."""
    return service.lease_transcript(chunk_id, node_id=node_id, epoch=epoch)


@fleet_router.post("/runners", status_code=201)
def register_runner(body: RunnerRegistrationBody, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    first = service.register(
        body.runner_id,
        workspace_id=body.workspace_id,
        url=body.url,
        redirect_uris=tuple(body.redirect_uris),
        env_capacity=body.env_capacity,
    )
    return {"runner_id": body.runner_id, "first_registration": first}


@fleet_router.get("/runners/{runner_id}")
def get_runner(runner_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    view = service.runner_view(runner_id)
    if view is None:
        return JSONResponse(status_code=404, content={"detail": f"unknown runner {runner_id}"})
    return view


@fleet_router.get("/questions/{question_id}")
def get_question(question_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.question_view(question_id)
    except QuestionNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.post("/chunks/{chunk_id}/leases", status_code=202)
def report_lease(
    chunk_id: str, body: LeaseReportBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    try:
        return service.report_lease(chunk_id, epoch=body.epoch, runner_id=body.runner_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.post("/chunks/{chunk_id}/escalations", status_code=202)
def report_escalation(
    chunk_id: str, body: EscalationReportBody, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    try:
        return service.report_escalation(
            chunk_id,
            epoch=body.epoch,
            runner_id=body.runner_id,
            takeover_command=body.takeover_command,
            wrapped_takeover_command=body.wrapped_takeover_command,
        )
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.post("/chunks/{chunk_id}/hub-advance")
def hub_advance(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.hub_advance(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
