"""The hub-gateway seam — the mock runner's outbound edge to a hub HTTP API.

The mock runner drives a hub exactly as the real runner does, but as a
controllable driver. Each call returns the raw ``(status_code, json)`` so the
service can observe and report exactly what the hub said.
"""

from __future__ import annotations

from typing import Any, Protocol


class IHubGateway(Protocol):
    """The mock runner's client of a hub API. Outbound-only, raw responses."""

    #: ``POST /api/runners`` — the operator add a runner's bootstrap makes when it holds no token.
    def add_runner(self, *, name: str) -> tuple[int, dict[str, Any]]: ...

    def register(
        self,
        *,
        name: str,
        workspace_id: str,
        capabilities: list[dict[str, Any]] | None = None,
        subscriptions: list[dict[str, Any]] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        """Register the runner the bearer token names, under ``name``. ``subscriptions`` is
        forwarded only when supplied — ``None`` omits the key entirely, driving the hub's
        rosterless fallback."""
        ...

    def peek(self) -> tuple[int, dict[str, Any]]: ...

    #: The matched fleet peek — ``POST /queue/peek`` under the bearer token, or none when ``enrolled`` is false.
    def peek_matched(
        self, *, enrolled: bool, capabilities: list[dict[str, Any]], policy: str
    ) -> tuple[int, dict[str, Any]]: ...
    def claim(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def submit_completion(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def get_chunk(self, chunk_id: str) -> tuple[int, dict[str, Any]]: ...

    #: ``GET /chunk-statuses`` — the runner tick's slim batch status read, one call
    #: across many chunk ids in place of many ``get_chunk`` round-trips.
    def chunk_statuses(self, chunk_ids: list[str]) -> tuple[int, dict[str, Any]]: ...

    #: ``POST /chunks/{id}/decisions`` — a runner-config gate decision
    #: (``DecisionSubmission{from_node_id, epoch, artifacts, route_token?}``).
    def submit_decision(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...

    #: ``GET /questions/{id}`` — the runner's answer poll.
    def get_question(self, question_id: str) -> tuple[int, dict[str, Any]]: ...

    #: The batched ``POST /events`` push — every fact this driver reports, ``lease.minted``
    #: and ``escalation.recorded`` included.
    def push_facts(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...

    #: The dedicated ``POST /transcripts`` route — the transcript
    #: lane's own push, structurally independent of ``push_facts``'s ``/events``.
    def push_transcripts(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
