"""The mock hub's domain model — a stateful stand-in for the real hub's HTTP surface.

These pydantic models mirror the subset of the hub OpenAPI a runner consumes,
reproduced here without importing ``blizzard``. ``ChunkSpec``/``NodeSpec`` is
the mock's own control vocabulary: a scripted graph an agent seeds.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# --- Enums mirrored from blizzard.foundation (value-identical) ---------------


class Executor(StrEnum):
    RUNNER = "runner"
    HUB = "hub"


class SessionMode(StrEnum):
    RESUME = "resume"
    FRESH = "fresh"


class JudgedBy(StrEnum):
    WORKER = "worker"
    HUMAN = "human"


class ChunkStatus(StrEnum):
    """The derived statuses the mock reports (subset of the real ``ChunkStatus``)."""

    NOT_READY = "not_ready"
    READY = "ready"
    PAUSED = "paused"
    RUNNING = "running"
    DELIVERING = "delivering"
    NEEDS_HUMAN = "needs_human"
    DONE = "done"
    STOPPED = "stopped"


#: The terminal statuses (mirrors ``chunk_status.TERMINAL_STATUSES``).
TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})

#: The unclaimed statuses deletion and a graph re-pin admit (mirrors ``chunk_status.PRE_CLAIM_STATUSES``).
PRE_CLAIM_STATUSES = frozenset({ChunkStatus.NOT_READY, ChunkStatus.READY})

#: The statuses an operator pause is legal from (mirrors the real ``ChunkVerb.PAUSE`` legality).
PAUSABLE_STATUSES = frozenset(ChunkStatus) - TERMINAL_STATUSES - {ChunkStatus.DELIVERING}


def status_if_paused(status: ChunkStatus) -> ChunkStatus:
    """The status a chunk at ``status`` derives once a pause settles — mirrors the real
    ``ChunkFacts.status_if_paused``: its current status where the pause is refused, or where
    ``needs_human`` outranks the pause on the real status ladder; ``paused`` otherwise."""
    if status not in PAUSABLE_STATUSES or status is ChunkStatus.NEEDS_HUMAN:
        return status
    return ChunkStatus.PAUSED


#: The reserved terminal node id a choice may point at (mirrors ``graph.RESERVED_TERMINAL``).
TERMINAL = "done"

#: The hub's own reserved work-source name (mirrors ``config.RESERVED_HUB_SOURCE_NAME``).
RESERVED_HUB_SOURCE_NAME = "hub"


class FindingExit(StrEnum):
    """How an exited finding left (mirrors ``blizzard.foundation.findings.FindingExit``)."""

    OUTFLOW = "outflow"
    WITHDRAWN = "withdrawn"


#: Each exit state's own exit (mirrors the real ``OUTFLOW_KINDS``/``WITHDRAWN_KINDS`` split):
#: the ground changed, or a person judged the finding rather than the code.
_FINDING_EXITS: dict[str, FindingExit] = {
    "resolved": FindingExit.OUTFLOW,
    "gone-confirmed": FindingExit.OUTFLOW,
    "wont-fix": FindingExit.WITHDRAWN,
    "not-a-finding": FindingExit.WITHDRAWN,
    "superseded": FindingExit.WITHDRAWN,
}


def finding_exit(state: str) -> FindingExit | None:
    """How a finding in ``state`` exited — ``None`` for a finding that has not exited
    (mirrors the real ``finding_exit``)."""
    return _FINDING_EXITS.get(state)


class GraphArtifactKind(StrEnum):
    """The one kind a graph-scoped artifact carries (subset of the real ``ArtifactKind``,
    which also has ``git_commit`` for node-scoped ones). The hub synthesizes ``asset`` for
    every graph-scope entry, so the seed vocabulary cannot express another kind — a mock
    that could would green a runner behavior the real hub never drives."""

    ASSET = "asset"


class ApplyOutcome(StrEnum):
    """Mirrors ``blizzard.foundation.node_steps.ApplyOutcome`` (value-identical)."""

    NEXT = "next"
    HUB_NODE_TAKEN = "hub_node_taken"
    PARKED_AT_GATE = "parked_at_gate"
    DONE = "done"
    FAILURE = "failure"


class GardenProposalClosureKind(StrEnum):
    """Mirrors ``blizzard.foundation.garden_proposals.GardenProposalClosureKind``
    (value-identical)."""

    PASSED = "passed"
    ACCEPTED = "accepted"


class GardenProposalItemOutcome(StrEnum):
    """Mirrors ``blizzard.foundation.garden_proposals.GardenProposalItemOutcome``
    (value-identical)."""

    MINTED = "minted"
    DECLINED = "declined"


class GardenProposalOrigin(StrEnum):
    """Mirrors ``blizzard.foundation.garden_proposals.GardenProposalOrigin``
    (value-identical)."""

    ROUTINE_RUN = "routine-run"
    OPERATOR = "operator"


class RoutineProposalState(StrEnum):
    """Mirrors ``blizzard.hub.domain.garden.proposals.model.RoutineProposalState``
    (value-identical) — the ``?state=`` selector on ``GET
    /chunks/{id}/garden/proposals``."""

    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


# --- The seed vocabulary (the mock's own control surface) --------------------


class ChoiceSpec(BaseModel):
    """One judgement outcome and the node it transitions to."""

    name: str
    description: str = ""
    to: str  # a node id in the same chunk, or ``TERMINAL``
    requires_checks: bool = False  # gate this edge on green checks


class RotatePolicySpec(BaseModel):
    """A declared session's rotation bounds — mirrors the hub's own."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class NodeSpec(BaseModel):
    """A scripted graph node an agent seeds. ``prompt`` rides straight into the envelope."""

    executor: Executor = Executor.RUNNER
    session: SessionMode = SessionMode.RESUME
    # The session reference target and effective declaration,
    # seeded per node (pinned by tests/test_mock_hub.py).
    session_source: str | None = None
    session_name: str | None = None
    session_model: list[str] = Field(default_factory=list)
    session_effort: str | None = None
    session_harnesses: list[str] = Field(default_factory=list)
    session_compaction_window: str | None = None
    session_rotate: RotatePolicySpec | None = None
    judged_by: JudgedBy = JudgedBy.WORKER
    prompt: str | None = None
    judgement_prompt: str | None = None
    choices: list[ChoiceSpec] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    checks_cwd: str | None = None  # where the runner runs `checks:`
    checks_timeout: int | None = None  # per-check timeout in seconds
    retries_max: int | None = None


class EscalationState(BaseModel):
    """Retries exhausted (``escalation.recorded``) — mirrors ``blizzard.wire.chunk.ChunkEscalationView``."""

    epoch: int
    takeover_command: str = ""
    #: The ``blizzard runner takeover`` wrapped entry point; empty whenever the
    #: runner didn't compose one.
    wrapped_takeover_command: str = ""
    cause: str | None = None
    detail: str | None = None


class QuestionState(BaseModel):
    """A pending or answered ask/answer rendezvous question (``question.asked``) —
    mirrors ``blizzard.wire.question.QuestionView``. Held in ``IHubState``, not on the
    chunk row, since a question is addressed by its own id (``GET /questions/{id}``)."""

    question_id: str
    chunk_id: str
    node_id: str | None = None
    session_id: str | None = None
    harness_id: str | None = None
    runner_id: str
    epoch: int
    question: str
    options: list[str] = Field(default_factory=list)
    asked_at: str
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None
    delivered: bool = False
    delivered_at: str | None = None

    def delivery_refusal(self, *, chunk_id: str) -> str | None:
        """Why an ``answer.delivered`` report naming ``chunk_id`` cannot land, or ``None``.
        Delivery is the answer's return trip to the asking session, so a report naming another
        chunk is refused, and so is one for a question not yet answered."""
        if chunk_id != self.chunk_id:
            return f"question {self.question_id} belongs to chunk {self.chunk_id}"
        if not self.answered:
            return f"question {self.question_id} is not answered"
        return None


class WorkRefSpec(BaseModel):
    """One ``{source, ref}`` work ref (D-105) — mirrors the hub's own pointer wire
    (``blizzard.wire.chunk.WorkRefModel``)."""

    source: str
    ref: str


class GraphArtifactSpec(BaseModel):
    """One graph-scoped artifact baked into the seeded chunk's mint — mirrors the hub's
    ``GraphArtifact`` wire model. ``kind`` is constrained to the only kind the real hub
    ever synthesizes at this slice, so seeding one is optional and naming any other is a
    422."""

    name: str
    kind: GraphArtifactKind = GraphArtifactKind.ASSET
    content: str


class SystemArtifactSpec(BaseModel):
    """One published ``ArtifactScope.SYSTEM`` document (``POST /_seed/system-artifacts``) —
    global, not tied to any seeded chunk, mirroring the hub's own packaged set. ``name`` may
    be slash-bearing, unlike a graph-scoped artifact's."""

    name: str
    content: str


class ScopeSpec(BaseModel):
    """One seeded scope (``POST /_seed/scopes``) — global, mirrors the real hub's own
    mint-on-name vocabulary rather than modeling its create/retire lifecycle: a scenario
    seeds the end state it wants directly. ``created_at`` defaults to mint time when
    omitted, the :class:`GardenProposalSpec` pattern."""

    slug: str
    description: str = ""
    created_at: str | None = None
    retired: bool = False


class GardenRunSpec(BaseModel):
    """A seeded chunk's garden run identity — mirrors the real hub's ``RunContext``,
    resolved server-side from the chunk rather than named by the caller. ``None`` on a
    chunk (the default) mirrors a chunk that is not a routine run at all."""

    routine_name: str
    scope_slug: str


class GardenFindingSpec(BaseModel):
    """One seeded finding on a chunk's garden run bucket — mirrors the fields the real
    hub's ``FindingView`` carries. Seeded live by default; the mock's own finding bucket
    has no exit-fact lever, so a non-live entry is only ever seeded that way directly."""

    model_config = ConfigDict(populate_by_name=True)

    finding_id: str
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None
    introduced_at: str | None = None
    first_observed_at: str | None = None
    live: bool = True
    state: str = "live"
    note: str | None = None
    last_seen_at: str | None = None
    observed_count: int = 0


class GardenAnsweredFindingSpec(GardenFindingSpec):
    """One seeded finding within a chunk's own answered-proposal set
    (``GET /chunks/{id}/findings``) — :class:`GardenFindingSpec`
    plus its own ``routine_name``/``scope_slug`` rather than deriving them from a seeded
    :class:`GardenRunSpec`: a minted chunk carries no run context at all, so the two
    reads' seeding levers stay independent."""

    routine_name: str
    scope_slug: str


class GardenProposalClosureSpec(BaseModel):
    """A seeded proposal's closure — mirrors the real hub's own
    ``GardenProposalClosureView``. A proposal seeded with none is served open; one
    seeded with a closure is served closed, under ``?state=closed`` or ``all``."""

    closure: GardenProposalClosureKind
    reason: str | None = None
    closed_by: str = "u_1"
    closed_at: str | None = None
    item_outcome: GardenProposalItemOutcome | None = None
    source: str | None = None
    ref: str | None = None


class GardenProposalSpec(BaseModel):
    """One seeded proposal on a chunk's garden run bucket — mirrors the fields the real
    hub's ``GardenProposalView`` carries, minus ``routine_name`` (supplied at the read
    from the chunk's own seeded :class:`GardenRunSpec`, exactly like
    :class:`GardenFindingSpec` derives its ``routine_name``/``scope_slug`` there). A
    proposal seeded with no ``closure`` is open; one seeded with one is closed.
    ``created_at`` defaults to mint time when omitted."""

    model_config = ConfigDict(populate_by_name=True)

    proposal_id: str
    class_: str = Field(alias="class")
    title: str
    body: str
    findings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    closure: GardenProposalClosureSpec | None = None


class AnalyticsCountRowSpec(BaseModel):
    """One seeded counts row — mirrors the real hub's ``AnalyticsCountView``.
    The mock does not aggregate: this row is served as-is, whatever
    window the caller names."""

    key: str
    count: int
    graph_name: str | None = None
    node_name: str | None = None


class AnalyticsSpendRowSpec(BaseModel):
    """One seeded spend row — mirrors the real hub's ``AnalyticsSpendView``.
    Served as-is, whatever window the caller names."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool = False
    graph_name: str | None = None
    node_name: str | None = None


class AnalyticsSpec(BaseModel):
    """A seeded chunk's canned analytics rows — one list per fleet
    counts/spend route, served whatever the caller's window, gated the same as
    ``garden_findings``/``garden_proposals`` on the chunk's own seeded
    :class:`GardenRunSpec` rather than a flag of its own."""

    counts_files: list[AnalyticsCountRowSpec] = Field(default_factory=list)
    counts_skills: list[AnalyticsCountRowSpec] = Field(default_factory=list)
    counts_agent_types: list[AnalyticsCountRowSpec] = Field(default_factory=list)
    counts_nodes: list[AnalyticsCountRowSpec] = Field(default_factory=list)
    spend_nodes: list[AnalyticsSpendRowSpec] = Field(default_factory=list)
    spend_graphs: list[AnalyticsSpendRowSpec] = Field(default_factory=list)


class ChunkSpec(BaseModel):
    """A seeded chunk: its scripted node graph plus work refs (POST /_seed/chunk)."""

    chunk_id: str | None = None
    graph_id: str = "gr_mock"
    #: The pinned graph's name the envelope reports; ``None`` models a hub that omits it.
    graph_name: str | None = None
    # Both default to express no preference, pinned by
    # tests/test_pin_mock.py.
    default_model: list[str] = Field(default_factory=list)
    default_effort: str | None = None
    # The chunk's default harness preference — the `default_model` shape.
    default_harnesses: list[str] = Field(default_factory=list)
    entry: str
    nodes: dict[str, NodeSpec]
    work_refs: list[WorkRefSpec] = Field(default_factory=list)
    graph_artifacts: list[GraphArtifactSpec] = Field(default_factory=list)
    #: The chunk's own run identity, or ``None`` for a chunk that is not a routine run.
    garden_run: GardenRunSpec | None = None
    garden_findings: list[GardenFindingSpec] = Field(default_factory=list)
    #: The findings the chunk's own accepted, minted garden proposal answers
    #: — ``None`` for a chunk answering no such proposal.
    garden_answered_findings: list[GardenAnsweredFindingSpec] | None = None
    #: The chunk's own routine's open proposal bucket, seeded per chunk exactly like
    #: ``garden_findings`` — the real ``GardenProposal`` carries no chunk at all.
    garden_proposals: list[GardenProposalSpec] = Field(default_factory=list)
    #: The chunk's own canned counts/spend rows — served as-is, gated on
    #: ``garden_run`` exactly like the garden reads above.
    analytics: AnalyticsSpec = Field(default_factory=AnalyticsSpec)
    #: The status seeded with no live route: ``ready``, or one a runner may not claim; never ``running``.
    status: ChunkStatus = ChunkStatus.READY

    @field_validator("status")
    @classmethod
    def _routeless(cls, status: ChunkStatus) -> ChunkStatus:
        if status is ChunkStatus.RUNNING:
            raise ValueError("a chunk is running only under a live route; claim it instead of seeding it running")
        return status


# --- The in-memory state row the service advances ----------------------------


class ChunkState(BaseModel):
    """The mock hub's mutable per-chunk state (facts collapsed to a small machine)."""

    chunk_id: str
    graph_id: str
    graph_name: str | None = None
    default_model: list[str] = Field(default_factory=list)
    default_effort: str | None = None
    default_harnesses: list[str] = Field(default_factory=list)
    entry: str
    nodes: dict[str, NodeSpec]
    work_refs: list[WorkRefSpec] = Field(default_factory=list)
    graph_artifacts: list[GraphArtifactSpec] = Field(default_factory=list)
    garden_run: GardenRunSpec | None = None
    garden_findings: list[GardenFindingSpec] = Field(default_factory=list)
    garden_answered_findings: list[GardenAnsweredFindingSpec] | None = None
    garden_proposals: list[GardenProposalSpec] = Field(default_factory=list)
    analytics: AnalyticsSpec = Field(default_factory=AnalyticsSpec)
    #: ``None`` until claimed; then the node the chunk is being worked at.
    current_node_id: str | None = None
    status: ChunkStatus = ChunkStatus.READY
    #: The fence: the newest ``lease.minted`` epoch reported for the chunk (D-044).
    latest_epoch: int = 0
    claimed: bool = False
    route_runner_id: str | None = None
    route_workspace_id: str | None = None
    route_environment_ids: list[str] = Field(default_factory=list)
    #: How many times the live route's capability token has been re-keyed,
    #: folded into the token string so a re-key never repeats.
    route_token_rekey_count: int = 0
    #: ``(from_node_id, epoch)`` -> the apply-response already produced, for idempotent
    #: re-apply (D-090): a replayed completion returns its original outcome, no re-advance.
    applied: dict[str, ApplyOutcome] = Field(default_factory=dict)
    #: The last apply-response produced, replayed verbatim by the ``replay`` lever
    #: (a duplicate delivery). Held as ``Any`` to avoid a cycle with ``domain.wire``.
    last_response: Any = None
    #: Retries exhausted (``escalation.recorded``) — surfaced
    #: read-only via ``ChunkDetail.escalation``.
    escalation: EscalationState | None = None

    def node(self, node_id: str) -> NodeSpec | None:
        return self.nodes.get(node_id)

    @property
    def ended(self) -> bool:
        """``done`` or ``stopped`` — the chunk has no node-step left to run."""
        return self.status in (ChunkStatus.DONE, ChunkStatus.STOPPED)

    def refuse_claim(self) -> None:
        """Refuse a claim the chunk cannot grant, in the real hub's order: an ended chunk
        (:class:`ClaimDeniedTerminal`), a held route (:class:`ClaimConflict`), then any status
        but ``ready`` (:class:`ClaimDeniedNotReady`)."""
        if self.ended:
            raise ClaimDeniedTerminal(chunk_id=self.chunk_id, status=self.status)
        if self.claimed:
            raise ClaimConflict(self.route_runner_id or "unknown")
        if self.status is not ChunkStatus.READY:
            raise ClaimDeniedNotReady(chunk_id=self.chunk_id, status=self.status)

    def refuse_rekey(self) -> None:
        """Refuse to re-key a route left on an ended chunk (:class:`RekeyDeniedTerminal`)."""
        if self.ended:
            raise RekeyDeniedTerminal(chunk_id=self.chunk_id, status=self.status)

    def refuse_envelope(self) -> None:
        """Refuse the envelope of an ended chunk (:class:`NoCurrentNode`)."""
        if self.ended:
            raise NoCurrentNode(self.chunk_id)

    def incoherent_attempt(self, *, from_node_id: str, epoch: int, open_question_ids: Sequence[str]) -> str | None:
        """Why the attempt at ``epoch`` cannot report out of ``from_node_id``, or ``None``: the
        report is not from the chunk's current node, the attempt's own escalation is open, or one
        of its own questions (``open_question_ids``, oldest first) is unanswered."""
        current = self.current_node_id
        if current is not None and from_node_id != current:
            return f"node `{from_node_id}` is not the chunk's current node `{current}` at epoch {epoch}"
        if self.escalation is not None and self.escalation.epoch == epoch:
            return f"the attempt at epoch {epoch} escalated — requeue the chunk before it moves on"
        if open_question_ids:
            return f"question {open_question_ids[0]} is open — answer it before the chunk moves on"
        return None

    def hub_executed(self, from_node_id: str) -> str | None:
        """Why a runner cannot report out of ``from_node_id``, or ``None``: the hub's own
        executor authors a hub-executed node's transitions."""
        node = self.node(from_node_id)
        if node is not None and node.executor is Executor.HUB:
            return f"node `{from_node_id}` is hub-executed — the hub authors its transitions"
        return None


def ships_from_lease_holder(owner: str | None, runner_id: str) -> bool:
    """Whether a transcript record shipped by ``runner_id`` comes from its epoch's owner. An
    epoch another runner owns refuses it; an epoch with no owner recorded yet admits it, since
    the fact lane that records the owner may trail the transcript lane."""
    return owner is None or owner == runner_id


# --- Refusals the model raises -------------------------------------------------


class ClaimConflict(Exception):
    """The chunk is already claimed — the losing runner gets a 409."""

    def __init__(self, held_by_runner_id: str) -> None:
        super().__init__(f"chunk already claimed by {held_by_runner_id}")
        self.held_by_runner_id = held_by_runner_id


class ClaimDeniedTerminal(Exception):
    """The chunk has ended — not a race loss: it can never be claimed again."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not claimable")
        self.chunk_id = chunk_id
        self.status = status


class ClaimDeniedNotReady(Exception):
    """The chunk is not ``ready`` and holds no live route, so the hub grants it to no runner."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not ready to claim")
        self.chunk_id = chunk_id
        self.status = status


class RekeyDeniedTerminal(Exception):
    """The live route sits on an ended chunk, where it confers no tenure — no token is minted."""

    def __init__(self, *, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, its route confers no tenure")
        self.chunk_id = chunk_id
        self.status = status


class NoCurrentNode(Exception):
    """The chunk has ended, so it has no node-step whose envelope could be read."""

    def __init__(self, chunk_id: str) -> None:
        super().__init__("chunk has no current runner node (terminal)")
        self.chunk_id = chunk_id
