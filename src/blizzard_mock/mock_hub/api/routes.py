"""The hub-mirror routes — the ``/api`` surface a runner consumes.

Vendor-native paths and JSON matching the real hub's OpenAPI subset.
Controllers hold only the ``MockHubService`` (``bzh:controller-read-only``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from blizzard_mock.mock_hub.api.deps import (
    CompletionBody,
    DecisionBody,
    HostAddBody,
    HostFactBatchBody,
    HostReportBody,
    HostRunnerAddBody,
    QueuePeekBody,
    RouteClaimBody,
    RunnerAddBody,
    RunnerFactBatchBody,
    RunnerRegistrationBody,
    RunnerRetireBody,
    TranscriptSegmentBatchBody,
    fleet_tenant_bound,
    get_host_principal,
    get_principal,
    get_service,
    get_tenant,
    host_tenant_bound,
    named_tenant,
    presented_bearer,
)
from blizzard_mock.mock_hub.domain.models import (
    ClaimConflict,
    ClaimDeniedNotReady,
    ClaimDeniedTerminal,
    NoCurrentNode,
    RekeyDeniedTerminal,
    RoutineProposalState,
)
from blizzard_mock.mock_hub.domain.service import (
    ChunkNotFound,
    ClaimIncompatible,
    DependencyUnmet,
    FindingNotInAnsweredSet,
    HostNameTaken,
    HostRetiredError,
    MalformedCursor,
    MockHubService,
    NoAnsweredProposal,
    NoRunContext,
    QuestionNotFound,
    RunnerAlreadyHosted,
    RunnerNeverConnected,
    RunnerRetired,
    RunnerTokenRefused,
    RunnerVersionRefused,
    SystemArtifactNotFound,
    UnknownHost,
    UnknownProjects,
    UnknownRunner,
)
from blizzard_mock.mock_hub.domain.state import (
    ClaimDeniedPaused,
    ClaimDeniedUnregistered,
    DeclaredSubscription,
    RunnerBinding,
    RunnerCapability,
    RunnerDecliningView,
)

#: Unauthenticated liveness — unaffected by the fleet partition, exactly as on the real
#: hub.
router = APIRouter(prefix="/api", tags=["hub"])

#: The runner-facing hub mirror — every route answers only a caller whose bearer token names a runner,
#: and acts in that runner's tenant.
fleet_router = APIRouter(
    prefix="/api/fleet", tags=["hub"], dependencies=[Depends(get_principal), Depends(fleet_tenant_bound)]
)

#: The host-facing hub mirror — every route answers only a caller whose bearer token names a host, and acts in that
#: host's tenant. A runner's token resolves no host here, and a host's resolves no runner on ``fleet_router``.
host_router = APIRouter(
    prefix="/api/fleet/host", tags=["hub"], dependencies=[Depends(get_host_principal), Depends(host_tenant_bound)]
)

#: ``GET /api/fleet/identity`` alone — outside the gate, since it is how a runner learns why its token is refused.
#: It resolves no tenant, so it reads no ``X-Blizzard-Tenant``.
identity_router = APIRouter(prefix="/api/fleet", tags=["hub"])

#: The operator verbs a runner's bootstrap calls, unauthenticated — each acts in the tenant
#: ``X-Blizzard-Tenant`` names, else the hub's only one.
operator_router = APIRouter(prefix="/api", tags=["hub"])

Tenant = Annotated[str, Depends(get_tenant)]

Principal = Annotated[str, Depends(get_principal)]


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready() -> dict[str, bool]:
    return {"ready": True}


@fleet_router.get("/queue/peek")
def peek_queue(service: Annotated[MockHubService, Depends(get_service)]) -> object:
    return service.peek()


@fleet_router.post("/queue/peek")
def peek_matched_queue(
    body: QueuePeekBody, runner_id: Principal, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """The matched fleet peek — at most one ready entry of a project the caller serves that its
    reported capabilities can both work and claim, ``body.policy`` applied to both. See
    :meth:`MockHubService.peek_matched` for the matching."""
    return service.peek_matched(
        runner_id,
        capabilities=tuple(
            RunnerCapability(
                harness_id=c.harness_id,
                version=c.version,
                tiers=tuple(c.tiers),
                default=c.default,
                available=c.available,
            )
            for c in body.capabilities
        ),
        policy=body.policy,
        project=body.project,
        projects=body.projects,
    )


@fleet_router.get("/system-artifacts")
def list_system_artifacts(service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """``ArtifactScope.SYSTEM``'s full published set, global rather than per-chunk."""
    return service.system_artifacts()


@fleet_router.get("/system-artifacts/{name:path}")
def get_system_artifact(name: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.system_artifact(name)
    except SystemArtifactNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/scopes")
def list_scopes(service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """The deployment's scope vocabulary, worker-facing."""
    return service.scopes()


@fleet_router.post("/routes", status_code=201)
def claim_route(
    body: RouteClaimBody, runner_id: Principal, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    try:
        return service.claim(
            body.chunk_id,
            runner_id=runner_id,
            workspace_id=body.workspace_id,
            environment_ids=body.environment_ids,
        )
    except ClaimDeniedUnregistered as exc:
        return JSONResponse(
            status_code=403, content={"chunk_id": body.chunk_id, "runner_id": exc.runner_id, "detail": str(exc)}
        )
    except ClaimDeniedPaused as exc:
        return JSONResponse(
            status_code=403,
            content={"chunk_id": body.chunk_id, "runner_id": exc.runner_id, "detail": "runner is paused at the hub"},
        )
    except ClaimDeniedTerminal as exc:
        return JSONResponse(
            status_code=409,
            content={"chunk_id": body.chunk_id, "status": exc.status.value, "detail": "chunk is terminal"},
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
    except ClaimDeniedNotReady as exc:
        # The status-carrying terminal shape: a runner skips the chunk as it does an ended one.
        return JSONResponse(
            status_code=409, content={"chunk_id": body.chunk_id, "status": exc.status.value, "detail": str(exc)}
        )
    except DependencyUnmet as exc:
        return JSONResponse(
            status_code=409,
            content={
                "chunk_id": body.chunk_id,
                "prerequisite_chunk_id": exc.prerequisite_chunk_id,
                "detail": "chunk depends on an unmet prerequisite",
            },
        )
    except ClaimIncompatible as exc:
        return JSONResponse(
            status_code=409,
            content={
                "chunk_id": body.chunk_id,
                "incompatible_runner_id": exc.runner_id,
                "detail": exc.detail,
                "reason": exc.reason,
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
    except RekeyDeniedTerminal as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}")
def get_chunk(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.chunk_detail(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunk-statuses")
def get_chunk_statuses(
    service: Annotated[MockHubService, Depends(get_service)],
    chunk_id: Annotated[list[str], Query()] = [],  # noqa: B006 — FastAPI's own idiom for repeatable query params
) -> object:
    """The runner tick's slim batch status read — repeatable ``chunk_id``; an unknown id
    is omitted, never a 404."""
    return service.chunk_statuses(chunk_id)


@fleet_router.get("/chunks/{chunk_id}/envelope")
def get_envelope(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.envelope(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoCurrentNode as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/work-items")
# Both the route and its deprecated `/pm-items` alias are served,
# pinned by tests/test_pin_mock.py.
@fleet_router.get("/chunks/{chunk_id}/pm-items")
def get_work_items(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.work_items(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/garden/findings")
def get_garden_findings(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """A worker-scoped read of the chunk's own routine's live finding bucket — mirrors
    the real hub's ``GET /api/fleet/chunks/{id}/garden/findings``."""
    try:
        return service.garden_findings(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/garden/proposals")
def get_garden_proposals(
    chunk_id: str,
    service: Annotated[MockHubService, Depends(get_service)],
    state: RoutineProposalState = RoutineProposalState.OPEN,
) -> object:
    """A worker-scoped read of the chunk's own routine's proposal bucket, filtered by
    ``state`` — mirrors the real hub's ``GET /api/fleet/chunks/{id}/garden/proposals``."""
    try:
        return service.garden_proposals(chunk_id, state=state)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/garden/proposals/page")
def get_garden_proposals_page(
    chunk_id: str,
    service: Annotated[MockHubService, Depends(get_service)],
    state: RoutineProposalState = RoutineProposalState.OPEN,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> object:
    """One keyset page of the chunk's own routine's proposal bucket — mirrors the real hub's
    ``GET /api/fleet/chunks/{id}/garden/proposals/page`` (limit bounds and 422 on a malformed
    cursor included)."""
    try:
        return service.garden_proposals_page(chunk_id, state=state, cursor=cursor, limit=limit)
    except (ChunkNotFound, NoRunContext) as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except MalformedCursor:
        return JSONResponse(status_code=422, content={"detail": "malformed cursor"})


@fleet_router.get("/chunks/{chunk_id}/analytics/counts/files")
def get_chunk_analytics_counts_files(
    chunk_id: str, since: Annotated[str, Query()], service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """A worker-scoped read of the chunk's own seeded counts-by-file rows — mirrors the
    real hub's ``GET /api/fleet/chunks/{id}/analytics/counts/files``.
    ``since`` is required for wire-contract fidelity with the real hub; the mock ignores
    its value and serves the seeded rows regardless (``bzh:wire-change-extends-mock``)."""
    try:
        return service.analytics_counts_files(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/analytics/counts/skills")
def get_chunk_analytics_counts_skills(
    chunk_id: str, since: Annotated[str, Query()], service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """Mirrors the real hub's ``GET /api/fleet/chunks/{id}/analytics/counts/skills``.
    ``since`` is required for wire-contract fidelity; the mock ignores its value."""
    try:
        return service.analytics_counts_skills(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/analytics/counts/agent-types")
def get_chunk_analytics_counts_agent_types(
    chunk_id: str, since: Annotated[str, Query()], service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """Mirrors the real hub's ``GET /api/fleet/chunks/{id}/analytics/counts/agent-types``.
    ``since`` is required for wire-contract fidelity; the mock ignores its value."""
    try:
        return service.analytics_counts_agent_types(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/analytics/counts/nodes")
def get_chunk_analytics_counts_nodes(
    chunk_id: str,
    since: Annotated[str, Query()],
    service: Annotated[MockHubService, Depends(get_service)],
    by_name: Annotated[bool, Query()] = False,
) -> object:
    """Mirrors the real hub's ``GET /api/fleet/chunks/{id}/analytics/counts/nodes``.
    ``since`` is required for wire-contract fidelity; the mock ignores its value, and ``by_name`` too:
    the mock serves its seeded rows as-is rather than aggregating."""
    try:
        return service.analytics_counts_nodes(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/analytics/spend/nodes")
def get_chunk_analytics_spend_nodes(
    chunk_id: str,
    since: Annotated[str, Query()],
    service: Annotated[MockHubService, Depends(get_service)],
    by_name: Annotated[bool, Query()] = False,
) -> object:
    """Mirrors the real hub's ``GET /api/fleet/chunks/{id}/analytics/spend/nodes``.
    ``since`` is required for wire-contract fidelity; the mock ignores its value, and ``by_name`` too:
    the mock serves its seeded rows as-is rather than aggregating."""
    try:
        return service.analytics_spend_nodes(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/analytics/spend/graphs")
def get_chunk_analytics_spend_graphs(
    chunk_id: str,
    since: Annotated[str, Query()],
    service: Annotated[MockHubService, Depends(get_service)],
    by_name: Annotated[bool, Query()] = False,
) -> object:
    """Mirrors the real hub's ``GET /api/fleet/chunks/{id}/analytics/spend/graphs``.
    ``since`` is required for wire-contract fidelity; the mock ignores its value, and ``by_name`` too:
    the mock serves its seeded rows as-is rather than aggregating."""
    try:
        return service.analytics_spend_graphs(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoRunContext as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/findings")
def get_chunk_findings(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """The findings the chunk's own accepted, minted garden proposal answers — mirrors
    the real hub's ``GET /api/fleet/chunks/{id}/findings``."""
    try:
        return service.answered_findings(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoAnsweredProposal as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.get("/chunks/{chunk_id}/findings/{finding_id}")
def get_chunk_finding(
    chunk_id: str, finding_id: str, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """One finding within the chunk's own answered set — mirrors the real hub's
    ``GET /api/fleet/chunks/{id}/findings/{finding_id}``."""
    try:
        return service.answered_finding(chunk_id, finding_id=finding_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except NoAnsweredProposal as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except FindingNotInAnsweredSet as exc:
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
def push_facts(
    body: RunnerFactBatchBody, runner_id: Principal, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    return service.ingest_facts(runner_id, [f.model_dump() for f in body.facts])


@fleet_router.post("/transcripts")
def push_transcripts(
    body: TranscriptSegmentBatchBody, runner_id: Principal, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """The transcript lane's own push — distinct from :func:`push_facts`,
    mirroring the real hub's ``POST /api/fleet/transcripts``."""
    return service.ingest_transcripts(runner_id, [r.model_dump() for r in body.records])


@fleet_router.get("/chunks/{chunk_id}/transcript-segments")
def get_lease_transcript_segments(
    chunk_id: str, node_id: str, epoch: int, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """A lease's retained transcript — mirrors the real hub's runner-scoped
    ``GET /api/fleet/chunks/{chunk_id}/transcript-segments``; the mock enforces no
    bearer-token confinement."""
    return service.lease_transcript(chunk_id, node_id=node_id, epoch=epoch)


@fleet_router.post("/runners", status_code=201)
def register_runner(
    body: RunnerRegistrationBody, runner_id: Principal, service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """Register the caller — the runner its bearer token names — under the name it declares."""
    subscriptions = (
        tuple(DeclaredSubscription(slug=d.slug, name=d.name, provider=d.provider) for d in body.subscriptions)
        if body.subscriptions is not None
        else None
    )
    try:
        return service.register(
            runner_id,
            name=body.name,
            workspace_id=body.workspace_id,
            url=body.url,
            redirect_uris=tuple(body.redirect_uris),
            env_capacity=body.env_capacity,
            capabilities=tuple(
                RunnerCapability(
                    harness_id=c.harness_id,
                    version=c.version,
                    tiers=tuple(c.tiers),
                    default=c.default,
                    available=c.available,
                )
                for c in body.capabilities
            ),
            subscriptions=subscriptions,
            gates=tuple(body.gates),
            projects=body.projects,
            bindings=(
                None
                if body.bindings is None
                else tuple(
                    RunnerBinding(project=b.project, workspace_id=b.workspace_id, agents=b.agents)
                    for b in body.bindings
                )
            ),
            subscription=body.subscription,
            agents=body.agents,
            declining=tuple(
                RunnerDecliningView(
                    lever=d.lever,
                    detail=d.detail,
                    subscription=d.subscription,
                    until=None if d.until is None else d.until.isoformat(),
                )
                for d in body.declining or ()
            ),
            version=body.version,
        )
    except RunnerVersionRefused as exc:
        return JSONResponse(status_code=409, content=exc.refusal.model_dump(mode="json"))


@fleet_router.get("/runners/{runner_id}")
def get_runner(runner_id: str, caller: Principal, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """The caller's own declarative state — 403 when the path names another runner, 409 before
    its first registration."""
    if runner_id != caller:
        return JSONResponse(
            status_code=403, content={"detail": f"token belongs to runner {caller!r}, not {runner_id!r}"}
        )
    try:
        view = service.runner_view(runner_id)
    except RunnerNeverConnected as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    if view is None:
        return JSONResponse(status_code=404, content={"detail": f"unknown runner {runner_id}"})
    return view


@identity_router.get("/identity")
def get_runner_identity(request: Request, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """The id and name of the runner the presented bearer token names, or a typed 401 saying why
    the hub refuses it. Registers nothing and records no liveness."""
    try:
        return service.identity(presented_bearer(request))
    except RunnerTokenRefused as exc:
        return JSONResponse(status_code=401, content={"reason": exc.reason, "runner_id": exc.runner_id})


@operator_router.post("/runners", status_code=201)
def add_runner(body: RunnerAddBody, tenant: Tenant, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Add a runner under the initial ``name`` in the request's tenant — its id and bearer token are
    minted together and the token returned once; it stays never connected until it registers with
    that token. It serves the ``projects`` named, each by id or slug — one naming no project of the
    tenant refuses the add ``422`` — or, naming none, the tenant's only project."""
    try:
        return service.add_runner(name=body.name, tenant_id=tenant, projects=body.projects, host_id=body.host_id)
    except UnknownProjects as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})
    except UnknownHost as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except HostRetiredError as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@operator_router.post("/hosts", status_code=201)
def add_host(body: HostAddBody, tenant: Tenant, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Add a host under ``name`` in the request's tenant — its id and bearer token are minted together and the token
    returned once. ``adopt`` places existing runners under it: one the tenant does not hold is ``404``, one under a
    host or retired ``409``, a name a live host holds ``409``, and nothing is added."""
    try:
        return service.add_host(name=body.name, tenant_id=tenant, adopt=body.adopt)
    except HostNameTaken as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})
    except UnknownRunner as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except RunnerAlreadyHosted as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@operator_router.get("/me")
def me(request: Request, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """The caller and the tenants it may enter — the implicit operator of a hub without sign-in, a
    member of every tenant. A header naming no tenant resolves none here, never a 404."""
    return service.me(named_tenant(request))


@operator_router.post("/runners/{runner_id}/enrollments", status_code=201)
def enroll_runner(runner_id: str, tenant: Tenant, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    """Rotate the runner's bearer token — 404 for a runner never added in the request's tenant, 409
    for a retired one."""
    try:
        return service.enroll(runner_id, tenant_id=tenant)
    except UnknownRunner as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    except RunnerRetired as exc:
        return JSONResponse(status_code=409, content={"detail": str(exc)})


@fleet_router.get("/questions/{question_id}")
def get_question(question_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.question_view(question_id)
    except QuestionNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@fleet_router.post("/chunks/{chunk_id}/hub-advance")
def hub_advance(chunk_id: str, service: Annotated[MockHubService, Depends(get_service)]) -> object:
    try:
        return service.hub_advance(chunk_id)
    except ChunkNotFound as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})


@host_router.post("")
def report_host(
    body: HostReportBody,
    host_id: Annotated[str, Depends(get_host_principal)],
    service: Annotated[MockHubService, Depends(get_service)],
) -> object:
    """Record the calling host's report — replacing its workspaces and subscriptions whole — and answer its record."""
    return service.report_host(
        host_id,
        name=body.name,
        url=body.url,
        redirect_uris=body.redirect_uris,
        workspaces=[(w.workspace_id, w.env_capacity) for w in body.workspaces],
        subscriptions=[DeclaredSubscription(slug=d.slug, name=d.name, provider=d.provider) for d in body.subscriptions],
    )


@host_router.get("")
def get_host_record(
    host_id: Annotated[str, Depends(get_host_principal)], service: Annotated[MockHubService, Depends(get_service)]
) -> object:
    """The calling host's own record and the runners standing under it that are not retired."""
    return service.host_record(host_id)


@host_router.post("/events")
def push_host_facts(
    body: HostFactBatchBody,
    host_id: Annotated[str, Depends(get_host_principal)],
    service: Annotated[MockHubService, Depends(get_service)],
) -> object:
    """Land the calling host's own facts — its subscription usage samples and misses — against a mark of the host's
    own, apart from every runner's."""
    return service.ingest_host_facts(host_id, [f.model_dump() for f in body.facts])


@host_router.post("/runners", status_code=201)
def add_host_runner(
    body: HostRunnerAddBody,
    host_id: Annotated[str, Depends(get_host_principal)],
    service: Annotated[MockHubService, Depends(get_service)],
) -> object:
    """Add a runner under the calling host, in the host's tenant — its id and bearer token are minted together and the
    token returned once; a ``projects`` entry no project of the tenant answers to refuses the add ``422``."""
    try:
        return service.add_host_runner(host_id, name=body.name, projects=body.projects)
    except UnknownProjects as exc:
        return JSONResponse(status_code=422, content={"detail": str(exc)})


@host_router.post("/runners/{runner_id}/retire")
def retire_host_runner(
    runner_id: str,
    body: RunnerRetireBody,
    host_id: Annotated[str, Depends(get_host_principal)],
    service: Annotated[MockHubService, Depends(get_service)],
) -> object:
    """Retire a runner under the calling host; a runner that does not stand under it is ``404``. The mock holds no
    routes to refuse over or release, so ``force`` changes nothing. The answer is the registry view's required fields
    and the ones the retire sets — a retired runner is never online."""
    try:
        runner = service.retire_host_runner(host_id=host_id, runner_id=runner_id, by=body.by)
    except UnknownRunner as exc:
        return JSONResponse(status_code=404, content={"detail": str(exc)})
    return {
        "runner": {
            "runner_id": runner.runner_id,
            "runner_name": runner.name,
            "connection": "never_connected" if runner.never_connected() else "offline",
            "online": False,
            "added_at": runner.added_at.isoformat(),
            "hub_paused": runner.paused,
            "retired": True,
            "retired_at": runner.retired_at.isoformat() if runner.retired_at is not None else None,
            "retired_by": runner.retired_by,
            "host_id": runner.host_id,
        },
        "released_chunk_ids": [],
    }
