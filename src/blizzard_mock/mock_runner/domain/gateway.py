"""The hub-gateway seam — the mock runner's outbound edge to a hub HTTP API.

The mock runner drives a hub exactly as the real runner does (``IHubClient``), but as a
*controllable* driver. This Protocol is the seam over that outbound edge; the httpx
adapter under ``internal/`` is the reference binding a service-tier test points at a real
(or mock) hub, and component tests inject an ``httpx.ASGITransport``-backed client to an
in-process mock hub — no live daemon. Each call returns the raw ``(status_code, json)`` so
the service can observe and report exactly what the hub said over the wire.
"""

from __future__ import annotations

from typing import Any, Protocol


class IHubGateway(Protocol):
    """The mock runner's client of a hub API. Outbound-only, raw responses."""

    def register(self, runner_id: str, workspace_id: str) -> tuple[int, dict[str, Any]]: ...
    def peek(self) -> tuple[int, dict[str, Any]]: ...
    def claim(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def report_lease(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def submit_completion(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]: ...
    def get_chunk(self, chunk_id: str) -> tuple[int, dict[str, Any]]: ...
