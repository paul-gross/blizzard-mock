"""The mock runner's driver state and control-request bodies.

``Held`` is the mock runner's copy of the lease it is working — the epoch it fences
completions with and the node it is at — mirroring the real runner's lease record. The
drive bodies are the mock's own control vocabulary (``/_drive/*``): a test tells the
driver which chunk to claim and which choice to complete with; the levers do the rest.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field


@dataclass
class Held:
    """A claimed chunk the driver is working: the fence epoch and the current node.

    ``route_token`` (issue #84b) is the plaintext the claim response returned once —
    stamped onto every subsequent chunk-scoped outbound call, mirroring the real
    runner's stash-and-stamp (``route_tokens`` table), unless a route-token lever
    overrides it for that one call."""

    chunk_id: str
    epoch: int
    from_node_id: str
    seq: int = 0
    route_token: str | None = None
    last_submission: dict[str, Any] = field(default_factory=dict)


class ClaimBody(BaseModel):
    """POST /_drive/claim — claim a chunk (its environments are cosmetic for the mock)."""

    chunk_id: str
    environment_ids: list[str] = Field(default_factory=lambda: ["e1"])


class CompleteBody(BaseModel):
    """POST /_drive/complete — complete the held node-step with a judgement choice.

    ``artifacts`` are the submission's ``produces:`` artifacts, each a wire
    ``SubmittedArtifact`` dict (``{name, kind, content, attached}``). A real runner's
    completion assembly (``_collect_asset_artifacts``) fills this from explicit
    ``blizzard runner attach`` writes (``attached=True``) and assessment fallbacks
    (``attached=False``); the mock lets a service test set them directly, so the hub's
    ``produces_mode=enforce`` backstop (issue #113 phase 5) can be driven over the wire —
    the produces analogue of the ``stale_route_token``/``omit_route_token`` levers. Default
    empty: every existing driver keeps submitting a completion with no artifacts."""

    chunk_id: str
    choice: str
    artifacts: list[dict[str, Any]] = Field(default_factory=list)


class ChunkQueryBody(BaseModel):
    """POST /_drive/get-chunk — read a chunk's detail over the wire."""

    chunk_id: str


class GitCommitDeclarationBody(BaseModel):
    """POST /api/leases/{lease_id}/git-commits — the served route's wire body (issue #143,
    Phase 3), mirroring the real runner's ``wire.git_commits.GitCommitDeclarationRequest``.
    ``forge`` is worker-declared (decision R7) — carried verbatim, no hub interaction: the
    declaration is purely local to the (mock) runner, exactly as it is on the real one."""

    forge: str
    repo: str
    branch: str
    commit: str


class DeclareGitCommitBody(BaseModel):
    """POST /_drive/declare-git-commit — drive a git-commit declaration directly against
    the mock runner's own local store (issue #143, Phase 3), the produces-kind analogue of
    ``CompleteBody.artifacts``: a service test can set declaration state without a raw
    client to the lease-scoped served route above, mirroring the ``/_drive/*`` convention
    every other verb here follows."""

    lease_id: str
    forge: str
    repo: str
    branch: str
    commit: str


class LeaseQueryBody(BaseModel):
    """POST /_drive/get-git-commits — read back a lease's declared git commits."""

    lease_id: str


class EscalateBody(BaseModel):
    """POST /_drive/escalate — report retries-exhausted via the dedicated route,
    fenced by the held lease's own epoch."""

    chunk_id: str
    takeover_command: str = ""


class DecideBody(BaseModel):
    """POST /_drive/decide — submit a decision at the held node (a runner-config gate
    parks the chunk). ``choice`` is accepted for drive-body symmetry but is cosmetic:
    the real ``DecisionSubmission`` carries no choice — resolution happens later,
    through the board's own decision-resolve action — mirroring ``ClaimBody.environment_ids``."""

    chunk_id: str
    choice: str | None = None


class AskBody(BaseModel):
    """POST /_drive/ask — push a ``question.asked`` fact, minting a pollable question
    hub-side."""

    chunk_id: str
    question: str
    options: list[str] = Field(default_factory=list)


class PollAnswerBody(BaseModel):
    """POST /_drive/poll-answer — GET the question's current answer state."""

    question_id: str


class PauseBody(BaseModel):
    """POST /_drive/pause — push a runner-scoped ``runner.locally_paused`` fact."""

    by: str = "operator"
    reason: str | None = None


class ResumeBody(BaseModel):
    """POST /_drive/resume — push a runner-scoped ``runner.locally_resumed`` fact."""

    by: str = "operator"


class ReportEventBody(BaseModel):
    """POST /_drive/report-event — push one ``event.recorded`` operational-event fact
    (issue #125). ``chunk_id`` is optional: a runner-scoped event names none."""

    severity: str
    kind: str
    message: str
    chunk_id: str | None = None
    lease_id: str | None = None
    node_name: str | None = None
    detail: dict[str, Any] | None = None
