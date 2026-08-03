"""The mock hub's domain model — a stateful stand-in for the real hub's HTTP surface.

These pydantic models **mirror the subset of the hub OpenAPI a runner consumes**
(``blizzard.wire.{queue,route,envelope,completion,chunk,facts,runner}``), reproduced
here **without importing ``blizzard``** — exactly as the mock forge mirrors the GitHub
REST surface without importing octokit. A real runner deserializes these responses with
its own wire models (non-strict ``model_validate``), so every field a wire model marks
required is present and named identically; mock-only extras are omitted.

The seed shape (``ChunkSpec`` / ``NodeSpec``) is the mock's own control vocabulary: a
scripted graph an agent seeds so a real runner (or the mock runner) can be driven
through it deterministically. ``ChunkState`` is the in-memory row the service advances.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# --- Enums mirrored from blizzard.hub.domain (value-identical) ---------------


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


#: The reserved terminal node id a choice may point at (mirrors ``graph.RESERVED_TERMINAL``).
TERMINAL = "done"


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
    # The session reference target and the effective declaration (issues #115, #144) —
    # seeded per node rather than derived from a graph-level `sessions:` map, because a
    # mock scenario scripts nodes directly and never mints a graph. A scenario seeds
    # exactly what the real hub would have resolved onto the envelope.
    session_source: str | None = None
    session_name: str | None = None
    session_model: list[str] = Field(default_factory=list)
    session_effort: str | None = None
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
    """Retries exhausted (``escalation.recorded``) — mirrors ``blizzard.wire.chunk.EscalationView``."""

    epoch: int
    takeover_command: str = ""
    #: The ``blizzard runner takeover`` wrapped entry point; empty whenever the runner
    #: didn't compose one — see ``blizzard-context:/domain/humans.md`` §Escalation for
    #: the full account of when each command is present.
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


class ChunkSpec(BaseModel):
    """A seeded chunk: its scripted node graph plus work refs (POST /_seed/chunk)."""

    chunk_id: str | None = None
    graph_id: str = "gr_mock"
    # The chunk's default model preference and effort (issue #144), mirroring the real
    # hub's `Chunk.default_model`/`default_effort` — what a surface declaring neither
    # inherits. Both default to *express no preference*, exactly as ingest mints them, so
    # a scenario that says nothing about either drives the same envelope the real hub does.
    default_model: list[str] = Field(default_factory=list)
    default_effort: str | None = None
    entry: str
    nodes: dict[str, NodeSpec]
    work_refs: list[WorkRefSpec] = Field(default_factory=list)


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
    #: (``POST /api/fleet/chunks/{id}/route-token``, issue paul-gross/blizzard#84b) —
    #: folded into the deterministic token string so a re-key never echoes the claim's
    #: own token back.
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
