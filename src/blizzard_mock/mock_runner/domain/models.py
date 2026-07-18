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
    """POST /_drive/complete — complete the held node-step with a judgement choice."""

    chunk_id: str
    choice: str


class ChunkQueryBody(BaseModel):
    """POST /_drive/get-chunk — read a chunk's detail over the wire."""

    chunk_id: str
