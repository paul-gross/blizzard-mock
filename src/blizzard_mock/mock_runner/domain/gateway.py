"""The hub-gateway seam — the mock runner's outbound edge to a hub HTTP API.

The mock runner drives a hub exactly as the real runner does, but as a
controllable driver. Each call returns the raw ``(status_code, json)`` so the
service can observe and report exactly what the hub said.
"""

from __future__ import annotations

from typing import Any, Protocol


class IHubGateway(Protocol):
    """The mock runner's client of a hub API. Outbound-only, raw responses."""

    def register(self, runner_id: str, *, workspace_id: str) -> tuple[int, dict[str, Any]]: ...
    def peek(self) -> tuple[int, dict[str, Any]]: ...
    def claim(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def submit_completion(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def get_chunk(self, chunk_id: str) -> tuple[int, dict[str, Any]]: ...

    #: The dedicated ``POST /chunks/{id}/leases`` route — the default
    #: transport; ``lease_via_events`` routes through the batched push instead.
    def report_lease_direct(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...

    #: The batched ``POST /events`` push, used for ``lease.minted`` only when the
    #: ``lease_via_events`` lever is armed — otherwise ``report_lease_direct`` is used.
    def report_lease_via_events(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...

    #: The dedicated ``POST /chunks/{id}/escalations`` route (``EscalationReport{epoch,
    #: runner_id, takeover_command, wrapped_takeover_command}``).
    def report_escalation(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...

    #: ``POST /chunks/{id}/decisions`` — a runner-config gate decision
    #: (``DecisionSubmission{from_node_id, epoch, runner_id, artifacts, route_token?}``).
    def submit_decision(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...

    #: ``GET /questions/{id}`` — the runner's answer poll.
    def get_question(self, question_id: str) -> tuple[int, dict[str, Any]]: ...

    #: The generic batched ``POST /events`` push, for facts that are not chunk-scoped
    #: leases (``question.asked``, ``runner.locally_paused``/``_resumed``).
    def push_facts(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
