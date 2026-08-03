"""Wire-mirror response bodies — byte-compatible with the hub OpenAPI a runner reads.

Every model here reproduces a ``blizzard.wire`` response shape field-for-field so a real
``HttpHubClient`` deserializes the mock's replies unchanged, **without ``blizzard`` being
a dependency of ``blizzard-mock``** (the forge mirrors GitHub the same way). Request
bodies the mock *accepts* are permissive dicts parsed in the service; only the responses
need shape fidelity.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from blizzard_mock.mock_hub.domain.models import ApplyOutcome, Executor, JudgedBy, SessionMode


class EnvelopeChoice(BaseModel):
    name: str
    description: str
    requires_checks: bool = False  # gate this edge on green checks (issue #114)


class RotatePolicyView(BaseModel):
    """A declared session's rotation bounds (issue #144) — mirrors
    ``blizzard.wire.envelope.RotatePolicyView``."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class NodeConfig(BaseModel):
    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    # The session reference target (issue #115) — the parsed `<name>` of a
    # `resume:<name>`/`fresh:<name>` form; null for the bare forms. Never picked up when
    # #115 landed, so the mock could not drive a targeted-resume envelope at all.
    session_source: str | None = None
    # The **effective** session declaration for this node-step (issue #144), already
    # merged hub-side (declaration over chunk default, field by field). The runner reads
    # these to resolve its pool, its model/effort, and its rotation bounds.
    session_name: str | None = None
    session_model: list[str] = Field(default_factory=list)
    session_effort: str | None = None
    session_rotate: RotatePolicyView | None = None
    judged_by: JudgedBy
    checks: list[str] = Field(default_factory=list)
    checks_cwd: str | None = None  # where the runner runs `checks:` (issue #114)
    checks_timeout: int | None = None  # per-check timeout in seconds (issue #114)
    produces: list[str] = Field(default_factory=list)
    retries_max: int | None = None
    mode: str | None = None
    choices: list[EnvelopeChoice] = Field(default_factory=list)


class NodeEnvelope(BaseModel):
    chunk_id: str
    graph_id: str
    epoch: int
    node: NodeConfig
    prompt: str | None
    judgement_prompt: str | None
    work_refs: list[dict[str, str]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class RouteClaimResponse(BaseModel):
    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    envelope: NodeEnvelope
    route_token: str


class RouteTokenRekeyResponse(BaseModel):
    chunk_id: str
    route_token: str


class RouteClaimConflict(BaseModel):
    chunk_id: str
    held_by_runner_id: str
    detail: str = "chunk already claimed"


class ApplyResponse(BaseModel):
    outcome: ApplyOutcome
    next_envelope: NodeEnvelope | None = None
    detail: str | None = None


class RouteView(BaseModel):
    runner_id: str
    workspace_id: str
    environment_ids: list[str] = Field(default_factory=list)


class EscalationView(BaseModel):
    epoch: int
    takeover_command: str
    # The ``blizzard runner takeover`` wrapped entry point (issue #251) — primary over
    # `takeover_command` whenever present; empty when the runner did not compose one.
    wrapped_takeover_command: str = ""


class QuestionView(BaseModel):
    question_id: str
    chunk_id: str
    node_id: str | None = None
    session_id: str | None = None
    runner_id: str
    epoch: int
    question: str
    options: list[str] = Field(default_factory=list)
    asked_at: str
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None
    # The return leg (blizzard#165): the ``answer.delivered`` fact landed, so the
    # resume-with-answer ran and the dormant session woke around it.
    delivered: bool = False
    delivered_at: str | None = None


class HubAdvanceResponse(BaseModel):
    chunk_id: str
    status: str
    ran: bool
    outcome_choice: str | None = None
    to_node_name: str | None = None
    detail: str = ""


class ChunkDetail(BaseModel):
    chunk_id: str
    graph_id: str
    status: str
    current_node_id: str | None
    latest_epoch: int | None
    work_refs: list[dict[str, str]] = Field(default_factory=list)
    # The chunk's default model preference and effort (issue #144), replacing #27's
    # `model` field-for-field with the real hub's — mirrored here so a real runner's wire
    # model deserializes the mock's replies unchanged (`bzh:wire-change-extends-mock`).
    default_model: list[str] = Field(default_factory=list)
    default_effort: str | None = None
    route: RouteView | None = None
    escalation: EscalationView | None = None
    questions: list[QuestionView] = Field(default_factory=list)


class QueuePeekEntry(BaseModel):
    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[dict[str, str]] = Field(default_factory=list)


class QueuePeekResponse(BaseModel):
    entries: list[QueuePeekEntry] = Field(default_factory=list)


class RunnerView(BaseModel):
    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    # Two brakes, mirroring the real hub's contract (blizzard#43): the fleet's, which the
    # runner pulls down and adheres to, and the runner's own, which it reports up. The mock
    # only models the first — it stands in for the hub, and the hub never sets the second.
    hub_paused: bool
    locally_paused: bool = False
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None


class RunnerFactAck(BaseModel):
    runner_id: str
    high_water: int
    applied: list[int] = Field(default_factory=list)
    already_applied: list[int] = Field(default_factory=list)
    rejected: list[int] = Field(default_factory=list)


class WorkItemEntry(BaseModel):
    """One pointer's pass-through work item — mirrors ``blizzard.wire.chunk.WorkItemEntry``.

    The mock carries no forge integration, so ``title``/``body`` are canned rather than
    vendor-fetched — the point of this route existing in the mock is the wire shape and
    the auth-header capture (issue #86b/#87), not work-item-content fidelity."""

    source: str
    ref: str
    label: str | None = None
    web_url: str | None = None
    fetched_at: str
    title: str | None = None
    body: str | None = None
    comments: list[str] = Field(default_factory=list)
    error: str | None = None


class WorkItemsView(BaseModel):
    items: list[WorkItemEntry] = Field(default_factory=list)
