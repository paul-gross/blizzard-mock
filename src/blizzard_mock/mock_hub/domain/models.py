"""The mock hub's domain model — a stateful stand-in for the real hub's HTTP surface.

These pydantic models mirror the subset of the hub OpenAPI a runner consumes,
reproduced here without importing ``blizzard``. ``ChunkSpec``/``NodeSpec`` is
the mock's own control vocabulary: a scripted graph an agent seeds.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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

    READY = "ready"
    RUNNING = "running"
    DELIVERING = "delivering"
    NEEDS_HUMAN = "needs_human"
    DONE = "done"
    STOPPED = "stopped"


#: The reserved terminal node id a choice may point at (mirrors ``graph.RESERVED_TERMINAL``).
TERMINAL = "done"


class GraphArtifactKind(StrEnum):
    """The one kind a graph-scoped artifact carries (subset of the real ``ArtifactKind``,
    which also has ``git_commit`` for node-scoped ones). The hub synthesizes ``asset`` for
    every graph-scope entry, so the seed vocabulary cannot express another kind — a mock
    that could would green a runner behavior the real hub never drives."""

    ASSET = "asset"


class ApplyOutcome(StrEnum):
    """Mirrors ``blizzard.wire.envelope.ApplyOutcome`` (value-identical)."""

    NEXT = "next"
    HUB_NODE_TAKEN = "hub_node_taken"
    PARKED_AT_GATE = "parked_at_gate"
    DONE = "done"
    FAILURE = "failure"


# --- The seed vocabulary (the mock's own control surface) --------------------


class ChoiceSpec(BaseModel):
    """One judgement outcome and the node it transitions to."""

    name: str
    description: str = ""
    to: str  # a node id in the same chunk, or ``TERMINAL``
    requires_checks: bool = False  # gate this edge on green checks (issue #114)


class RotatePolicySpec(BaseModel):
    """A declared session's rotation bounds (issue #144) — mirrors the hub's own."""

    max_context_tokens: int | None = None
    max_transcript_bytes: int | None = None
    max_invocations: int | None = None


class NodeSpec(BaseModel):
    """A scripted graph node an agent seeds. ``prompt`` rides straight into the envelope."""

    executor: Executor = Executor.RUNNER
    session: SessionMode = SessionMode.RESUME
    # The session reference target and effective declaration (issues #115, #144),
    # seeded per node (pinned by tests/test_mock_hub.py).
    session_source: str | None = None
    session_name: str | None = None
    session_model: list[str] = Field(default_factory=list)
    session_effort: str | None = None
    session_compaction_window: str | None = None
    session_rotate: RotatePolicySpec | None = None
    judged_by: JudgedBy = JudgedBy.WORKER
    prompt: str | None = None
    judgement_prompt: str | None = None
    choices: list[ChoiceSpec] = Field(default_factory=list)
    produces: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    checks_cwd: str | None = None  # where the runner runs `checks:` (issue #114)
    checks_timeout: int | None = None  # per-check timeout in seconds (issue #114)
    retries_max: int | None = None
    mode: str | None = None  # hub node: merge-to-main | open-pr


class EscalationState(BaseModel):
    """Retries exhausted (``escalation.recorded``) — mirrors ``blizzard.wire.chunk.ChunkEscalationView``."""

    epoch: int
    takeover_command: str = ""
    #: The ``blizzard runner takeover`` wrapped entry point; empty whenever the
    #: runner didn't compose one.
    wrapped_takeover_command: str = ""


class QuestionState(BaseModel):
    """A pending or answered ask/answer rendezvous question (``question.asked``) —
    mirrors ``blizzard.wire.question.QuestionView``. Held in ``IHubState``, not on the
    chunk row, since a question is addressed by its own id (``GET /questions/{id}``)."""

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
    delivered: bool = False
    delivered_at: str | None = None


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
    live: bool = True
    state: str = "live"
    note: str | None = None
    last_seen_at: str | None = None
    observed_count: int = 0


class ChunkSpec(BaseModel):
    """A seeded chunk: its scripted node graph plus work refs (POST /_seed/chunk)."""

    chunk_id: str | None = None
    graph_id: str = "gr_mock"
    # Both default to express no preference (issue #144), pinned by
    # tests/test_pin_mock.py.
    default_model: list[str] = Field(default_factory=list)
    default_effort: str | None = None
    entry: str
    nodes: dict[str, NodeSpec]
    work_refs: list[WorkRefSpec] = Field(default_factory=list)
    graph_artifacts: list[GraphArtifactSpec] = Field(default_factory=list)
    #: The chunk's own run identity, or ``None`` for a chunk that is not a routine run.
    garden_run: GardenRunSpec | None = None
    garden_findings: list[GardenFindingSpec] = Field(default_factory=list)


# --- The in-memory state row the service advances ----------------------------


class ChunkState(BaseModel):
    """The mock hub's mutable per-chunk state (facts collapsed to a small machine)."""

    chunk_id: str
    graph_id: str
    default_model: list[str] = Field(default_factory=list)
    default_effort: str | None = None
    entry: str
    nodes: dict[str, NodeSpec]
    work_refs: list[WorkRefSpec] = Field(default_factory=list)
    graph_artifacts: list[GraphArtifactSpec] = Field(default_factory=list)
    garden_run: GardenRunSpec | None = None
    garden_findings: list[GardenFindingSpec] = Field(default_factory=list)
    #: ``None`` until claimed; then the node the chunk is being worked at.
    current_node_id: str | None = None
    status: ChunkStatus = ChunkStatus.READY
    #: The fence: the newest ``lease.minted`` epoch reported for the chunk (D-044).
    latest_epoch: int = 0
    claimed: bool = False
    route_runner_id: str | None = None
    route_workspace_id: str | None = None
    route_environment_ids: list[str] = Field(default_factory=list)
    #: How many times the live route's capability token has been re-keyed
    #: (issue #84b); folded into the token string so a re-key never repeats.
    route_token_rekey_count: int = 0
    #: ``(from_node_id, epoch)`` -> the apply-response already produced, for idempotent
    #: re-apply (D-090): a replayed completion returns its original outcome, no re-advance.
    applied: dict[str, ApplyOutcome] = Field(default_factory=dict)
    #: The last apply-response produced, replayed verbatim by the ``replay`` lever
    #: (a duplicate delivery). Held as ``Any`` to avoid a cycle with ``domain.wire``.
    last_response: Any = None
    #: Retries exhausted (``escalation.recorded`` / ``POST .../escalations``) — surfaced
    #: read-only via ``ChunkDetail.escalation``.
    escalation: EscalationState | None = None

    def node(self, node_id: str) -> NodeSpec | None:
        return self.nodes.get(node_id)
