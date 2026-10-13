"""Shared FastAPI dependencies and request bodies for the mock-hub routers.

The composition root stashes the wired ``MockHubService`` and lever store on ``app.state``;
routers reach them through these dependencies (``bzh:dependency-injection``). Request bodies
name only the fields the mock reads, except ``MirroredWireBody`` subclasses, which mirror
field-for-field including required-ness."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from blizzard_mock.mock_hub.domain.capture import ICaptureStore
from blizzard_mock.mock_hub.domain.service import (
    TENANT_HEADER,
    ForeignTenantNamed,
    HostRetiredError,
    MockHubService,
    NoSuchTenant,
    TenantRequired,
    UnresolvableHost,
    UnresolvableRunner,
)


def get_service(request: Request) -> MockHubService:
    service: MockHubService = request.app.state.service
    return service


def presented_bearer(request: Request) -> str | None:
    """The bearer token an ``Authorization: Bearer <token>`` header presents, else ``None``."""
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def get_principal(request: Request, service: Annotated[MockHubService, Depends(get_service)]) -> str:
    """The calling runner's id, resolved from its bearer token — the one identity a fleet call
    carries. A missing, revoked or unknown token is refused ``401`` before any handler runs, as
    the real fleet router's gate does; a body ``runner_id`` is never read."""
    try:
        return service.principal(presented_bearer(request))
    except UnresolvableRunner as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def get_host_principal(request: Request, service: Annotated[MockHubService, Depends(get_service)]) -> str:
    """The calling host's id, resolved from its bearer token — the one identity a host call carries. A missing,
    revoked or unknown token is refused ``401`` and a retired host's ``403``, before any handler runs, as the real
    host gate does; a runner's token resolves no host."""
    try:
        return service.host_principal(presented_bearer(request))
    except UnresolvableHost as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except HostRetiredError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def named_tenant(request: Request) -> str | None:
    """The tenant id an ``X-Blizzard-Tenant`` header names, else ``None``."""
    return request.headers.get(TENANT_HEADER) or None


def fleet_tenant_bound(
    request: Request,
    runner_id: Annotated[str, Depends(get_principal)],
    service: Annotated[MockHubService, Depends(get_service)],
) -> None:
    """A fleet router dependency after :func:`get_principal`: the runner token decides the call's
    tenant, so an ``X-Blizzard-Tenant`` naming another one is refused ``400``, as the real hub's
    resolution does; one naming the token's own tenant is accepted."""
    try:
        service.refuse_foreign_tenant(runner_id, named_tenant(request))
    except ForeignTenantNamed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def host_tenant_bound(
    request: Request,
    host_id: Annotated[str, Depends(get_host_principal)],
    service: Annotated[MockHubService, Depends(get_service)],
) -> None:
    """A host router dependency after :func:`get_host_principal`: the host token decides the call's tenant, so an
    ``X-Blizzard-Tenant`` naming another one is refused ``400``."""
    try:
        service.refuse_foreign_host_tenant(host_id, named_tenant(request))
    except ForeignTenantNamed as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def get_tenant(request: Request, service: Annotated[MockHubService, Depends(get_service)]) -> str:
    """The tenant an operator call acts in — the one ``X-Blizzard-Tenant`` names (``404`` for an id
    no tenant holds, the real hub's one body for unknown and unenterable), else the hub's only
    tenant (``409 tenant_required`` when it holds several). The mock's operator is a member of
    every tenant, as the real hub's implicit operator is."""
    try:
        return service.tenant_named(named_tenant(request))
    except NoSuchTenant as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except TenantRequired as exc:
        raise HTTPException(status_code=409, detail={"code": "tenant_required", "message": str(exc)}) from exc


def get_captured(request: Request) -> ICaptureStore:
    captured: ICaptureStore = request.app.state.captured
    return captured


class MirroredWireBody(BaseModel):
    """Marker base for a request body meant to mirror a real wire schema field-for-field
    (`bzh:wire-change-extends-mock`) — lets ``test_wire_parity.py`` discover the full set
    of intended mirrors mechanically, the request-body counterpart to the
    response-model mirror module's own module-membership scan."""


class RunnerCapabilityBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.runner.RunnerCapability`` field-for-field — the one nested
    shape inside ``RunnerRegistrationBody`` promoted to a checked mirror, since a silent
    rename there would otherwise round-trip as a dropped field rather than failing the
    tier. ``RunnerRegistrationBody`` itself stays the mock's usual unchecked body."""

    harness_id: str
    version: str | None = None
    tiers: list[str] = Field(default_factory=list)
    default: bool = False
    available: bool = True


class RunnerSubscriptionDeclarationBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.runner.RunnerSubscriptionDeclaration`` field-for-field —
    the roster element inside ``RunnerRegistrationBody``, promoted to a checked mirror
    the same way :class:`RunnerCapabilityBody` is."""

    slug: str
    name: str
    provider: str


class QueuePeekBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.queue.QueuePeekRequest`` field-for-field. ``capabilities``
    reuses :class:`RunnerCapabilityBody` for its element shape. Carries no ``runner_id``
    — the verb answers for the bearer token's principal alone."""

    capabilities: list[RunnerCapabilityBody] = Field(default_factory=list)
    policy: str = "pass-over"
    # Narrows the peek to one project of the runner's declaration, by entry or resolved id.
    project: str | None = None


class RouteClaimBody(BaseModel):
    chunk_id: str
    workspace_id: str = "workspace-mock"
    environment_ids: list[str] = Field(default_factory=list)


class CompletionBody(BaseModel):
    choice: str
    epoch: int
    from_node_id: str
    check_results: list[dict[str, object]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class DecisionBody(BaseModel):
    from_node_id: str
    epoch: int
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class RunnerBindingBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.runner.RunnerBinding`` field-for-field."""

    project: str
    workspace_id: str
    agents: int


class RunnerRegistrationBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.runner.RunnerRegistrationRequest`` field-for-field. It carries
    no identity — the bearer token names the runner — only the ``name`` it declares, which a
    blank or absent value leaves as the hub holds it."""

    name: str | None = None
    workspace_id: str = "workspace-mock"
    # The runner's configured environment-pool size — None when it reports none.
    env_capacity: int | None = None
    # The runner's optional federation identity — round-tripped
    # into `MockHubService.register`, mirroring the real hub.
    url: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)
    # The runner's capability snapshot — every harness/tier it can execute right now.
    capabilities: list[RunnerCapabilityBody] = Field(default_factory=list)
    # The runner's declared subscription roster — kept distinct from an absent one.
    subscriptions: list[RunnerSubscriptionDeclarationBody] | None = None
    # The node names the runner holds for a human decision — reported, never enforced.
    gates: list[str] = Field(default_factory=list)
    # The projects the runner serves, by id or slug — ``None`` keeps the declaration the hub holds.
    projects: list[str] | None = None
    # The projects it serves through its host's workspaces; sent, they replace ``projects`` as the declaration.
    bindings: list[RunnerBindingBody] | None = None
    # The host subscription slug every binding spends.
    subscription: str | None = None


class RunnerFactBody(BaseModel):
    seq: int = 0
    kind: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class RunnerFactBatchBody(BaseModel):
    facts: list[RunnerFactBody] = Field(default_factory=list)


class HostFactBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.facts.RunnerFact`` field-for-field — one buffered fact on a host's lane, whose item shape
    is the runner lane's."""

    seq: int
    kind: str
    payload: dict[str, object] = Field(default_factory=dict)


class HostFactBatchBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.facts.HostFactBatch`` field-for-field — a host's push, ordered by seq."""

    facts: list[HostFactBody]


class ToolCallSegmentBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.transcript_segment.ToolCallSegmentView`` field-for-field,
    required-ness included, so a real field rename fails `service-test` rather than
    shipping green. ``input_truncated`` mirrors the real view's own default instead."""

    name: str
    input: dict[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool
    input_truncated: bool = False
    output_patch: bool = False


class SidechainSegmentBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.transcript_segment.SidechainSegmentView`` field-for-field,
    including required-ness."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[TurnSegmentBody]
    parent_tool_use_id: str | None = None


class TurnSegmentBody(MirroredWireBody):
    """Mirrors ``TurnSegmentView`` field-for-field, including required-ness — the mock
    still never *interprets* turn content, but a typed, non-defaulted shape here fails
    validation on a real field rename or drop instead of silently passing through as an
    untyped dict or a filled-in default."""

    index: int
    kind: str
    timestamp: str | None
    text: str
    tool: ToolCallSegmentBody | None
    thinking_redacted: bool
    sidechain: SidechainSegmentBody | None
    truncated: bool


SidechainSegmentBody.model_rebuild()


class TranscriptSegmentRecordBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.transcript_segment.TranscriptSegmentRecord``
    field-for-field, including required-ness — as exposed to a silent rename as the turn
    bodies above, bar ``record_truncated``, which the real model defaults too."""

    seq: int
    segment_id: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    turn_range_start: int
    turn_range_end: int
    final: bool
    harness_id: str | None = None
    normalizer_version: str
    harness_version: str | None
    model: str | None = None
    effort: str | None = None
    spawn_cwd: str | None = None
    record_truncated: bool = False
    supersedes: str | None = None
    turns: list[TurnSegmentBody]


class TranscriptSegmentBatchBody(MirroredWireBody):
    records: list[TranscriptSegmentRecordBody] = Field(default_factory=list)


class RunnerAddBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.runner.RunnerAddRequest`` field-for-field — stripped, and a blank
    name refused ``422``."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    # The projects the runner is added serving, by id or slug — ``None`` serves the tenant's only project.
    projects: list[str] | None = None
    # The host the runner is added under; an unknown or retired one refuses the add.
    host_id: str | None = None


class HostWorkspaceBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.host.HostWorkspace`` field-for-field — one workspace a host reports."""

    workspace_id: str
    env_capacity: int | None = None


class HostReportBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.host.HostReportRequest`` field-for-field — what the calling host reports about itself.
    ``workspaces`` and ``subscriptions`` replace what the hub holds, whole."""

    name: str | None = None
    url: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)
    workspaces: list[HostWorkspaceBody] = Field(default_factory=list)
    subscriptions: list[RunnerSubscriptionDeclarationBody] = Field(default_factory=list)


class HostRunnerAddBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.host.HostRunnerAddRequest`` field-for-field — a runner added under the calling host."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    projects: list[str] | None = None


class HostAddBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.host.HostAddRequest`` field-for-field — stripped, and a blank name refused ``422``."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    adopt: list[str] = Field(default_factory=list)


class RunnerRetireBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.runner.RunnerRetireRequest`` field-for-field — who retires, and whether to ``force``."""

    by: str = "operator"
    force: bool = False


class RunnerSeedControlBody(BaseModel):
    """``POST /_seed/runners`` — test-control only; adds a runner never connected, as
    ``POST /api/runners`` does, but may pin its id and token so a scenario can name both."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)] = "runner-mock"
    runner_id: str | None = None
    token: str | None = None
    # The tenant the runner is added in — the hub's first tenant when omitted.
    tenant_id: str | None = None
    # The projects it is added serving, resolved as ``POST /api/runners`` resolves them.
    projects: list[str] | None = None


class ProjectSeedControlBody(BaseModel):
    """``POST /_seed/projects`` — test-control only; creates a project in a tenant (the hub's first tenant when
    none is named), minting its ``proj_`` id unless a scenario pins one."""

    slug: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    tenant_id: str | None = None
    project_id: str | None = None


class RunnerProjectsControlBody(BaseModel):
    """``PUT /_seed/runners/{runner_id}/projects`` — test-control only; replaces the runner's served
    declaration as a registration declaring ``projects`` does, without the registration."""

    projects: list[str]


class TenantSeedControlBody(BaseModel):
    """``POST /_seed/tenants`` — test-control only; creates a tenant, minting its ``ten_`` id
    unless a scenario pins one."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    tenant_id: str | None = None


class TenantRenameControlBody(BaseModel):
    """``PATCH /_seed/tenants/{tenant_id}`` — test-control only; renames a tenant, as the hub
    administrator's rename does."""

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RunnerRetireControlBody(BaseModel):
    """``POST /_seed/runners/{runner_id}/retire`` — test-control only; plays the operator's
    retire verb, which revokes the runner's token in the same pass."""

    by: str = "operator"


class AnswerControlBody(BaseModel):
    """``POST /_seed/answer`` — test-control only; plays the operator's answer so a
    scenario can make the runner's poll return ``answered=True`` without a real
    operator surface (the fleet mirror carries no board-facing answer route)."""

    question_id: str
    answer: str
    answered_by: str = "operator"


class StopControlBody(BaseModel):
    """``POST /_seed/stop`` — test-control only; plays the operator's stop verb so a
    scenario can drive a seeded chunk to ``stopped`` (the fleet mirror carries no
    board-facing stop route)."""

    chunk_id: str
