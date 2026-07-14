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


class NodeConfig(BaseModel):
    node_id: str
    node_name: str
    executor: Executor
    session: SessionMode
    judged_by: JudgedBy
    checks: list[str] = Field(default_factory=list)
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
    pm_pointers: list[dict[str, str]] = Field(default_factory=list)
    artifacts: list[dict[str, object]] = Field(default_factory=list)


class RouteClaimResponse(BaseModel):
    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    envelope: NodeEnvelope


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


class ChunkDetail(BaseModel):
    chunk_id: str
    graph_id: str
    status: str
    current_node_id: str | None
    latest_epoch: int | None
    pm_pointers: list[dict[str, str]] = Field(default_factory=list)
    route: RouteView | None = None


class QueuePeekEntry(BaseModel):
    chunk_id: str
    graph_id: str
    position: int
    pm_pointers: list[dict[str, str]] = Field(default_factory=list)


class QueuePeekResponse(BaseModel):
    entries: list[QueuePeekEntry] = Field(default_factory=list)


class RunnerView(BaseModel):
    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    paused: bool


class RunnerFactAck(BaseModel):
    runner_id: str
    high_water: int
    applied: list[int] = Field(default_factory=list)
    already_applied: list[int] = Field(default_factory=list)
    rejected: list[int] = Field(default_factory=list)
