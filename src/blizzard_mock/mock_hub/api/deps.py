"""Shared FastAPI dependencies and request bodies for the mock-hub routers.

The composition root stashes the wired ``MockHubService`` and lever store on
``app.state``; routers reach them through these dependencies
(``bzh:dependency-injection``). Request bodies name only the fields the mock reads —
EXCEPT the transcript segment mirror bodies, field-for-field including required-ness."""

from __future__ import annotations

from fastapi import Request
from pydantic import BaseModel, Field

from blizzard_mock.mock_hub.domain.capture import ICaptureStore
from blizzard_mock.mock_hub.domain.service import MockHubService


def get_service(request: Request) -> MockHubService:
    service: MockHubService = request.app.state.service
    return service


def get_captured(request: Request) -> ICaptureStore:
    captured: ICaptureStore = request.app.state.captured
    return captured


class RouteClaimBody(BaseModel):
    chunk_id: str
    runner_id: str
    workspace_id: str = "workspace-mock"
    environment_ids: list[str] = Field(default_factory=list)


class CompletionBody(BaseModel):
    choice: str
    epoch: int
    runner_id: str = "runner-mock"
    from_node_id: str
    check_results: list[dict[str, object]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class DecisionBody(BaseModel):
    from_node_id: str
    epoch: int
    runner_id: str = "runner-mock"
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class RunnerRegistrationBody(BaseModel):
    runner_id: str
    workspace_id: str = "workspace-mock"
    # The runner's configured environment-pool size — None when it reports none.
    env_capacity: int | None = None
    # The runner's optional federation identity (issue #95) — round-tripped
    # into `MockHubService.register`, mirroring the real hub.
    url: str | None = None
    redirect_uris: list[str] = Field(default_factory=list)


class RunnerFactBody(BaseModel):
    seq: int = 0
    kind: str = ""
    payload: dict[str, object] = Field(default_factory=dict)


class RunnerFactBatchBody(BaseModel):
    runner_id: str
    facts: list[RunnerFactBody] = Field(default_factory=list)


class MirroredWireBody(BaseModel):
    """Marker base for a request body meant to mirror a real wire schema field-for-field
    (`bzh:wire-change-extends-mock`) — lets ``test_wire_parity.py`` discover the full set
    of intended mirrors mechanically (F10), the request-body counterpart to the
    response-model mirror module's own module-membership scan."""


class ToolCallSegmentBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.transcript_segment.ToolCallSegmentView`` field-for-field,
    including which fields are REQUIRED — a defaulted field here would let a real field
    rename ship green through `service-test` (which drives the mock, not the real hub)
    with no warning, same as a freeform ``dict`` would. ``input_truncated`` is the one
    deliberate exception: it mirrors the real view's own default too (review round 6 F4),
    since matching required-ness means matching defaultedness for a field the real hub
    added forward-compatibly."""

    name: str
    input: dict[str, object]
    input_unparsed: str | None
    input_shape: str
    tool_use_id: str | None
    output: str | None
    output_truncated: bool
    input_truncated: bool = False


class SidechainSegmentBody(MirroredWireBody):
    """Mirrors ``blizzard.wire.transcript_segment.SidechainSegmentView`` field-for-field,
    including required-ness."""

    agent_id: str | None
    agent_type: str | None
    link: str
    turns: list[TurnSegmentBody]


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
    """Mirrors ``blizzard.wire.transcript_segment.TranscriptSegmentRecord`` (blizzard#247)
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
    normalizer_version: str
    harness_version: str | None
    record_truncated: bool = False
    turns: list[TurnSegmentBody]


class TranscriptSegmentBatchBody(MirroredWireBody):
    runner_id: str
    records: list[TranscriptSegmentRecordBody] = Field(default_factory=list)


class PauseBody(BaseModel):
    paused: bool = True


class LeaseReportBody(BaseModel):
    """``POST /chunks/{id}/leases`` — mirrors ``blizzard.wire.facts.LeaseMintReport``."""

    epoch: int
    runner_id: str = "runner-mock"


class EscalationReportBody(BaseModel):
    """``POST /chunks/{id}/escalations`` — mirrors ``blizzard.wire.facts.EscalationReport``."""

    epoch: int
    runner_id: str = "runner-mock"
    takeover_command: str = ""
    wrapped_takeover_command: str = ""


class AnswerControlBody(BaseModel):
    """``POST /_seed/answer`` — test-control only; plays the operator's answer so a
    scenario can make the runner's poll return ``answered=True`` without a real
    operator surface (the fleet mirror carries no board-facing answer route)."""

    question_id: str
    answer: str
    answered_by: str = "operator"
