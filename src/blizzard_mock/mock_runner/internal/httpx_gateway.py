"""httpx binding for the hub-gateway seam (package-private, ``bzh:dependency-inversion``).

All httpx usage is confined here. A transport failure surfaces as ``(0, {...})`` so the
service treats an unreachable hub uniformly with a 5xx — the mock runner is a test
instrument, so it reports rather than raises.
"""

from __future__ import annotations

from typing import Any

import httpx

#: Every mock-runner->real-hub call (issue #87) — the real hub's fleet router mounts
#: `require_runner_principal` once at router level, so a mock runner driving the real
#: hub in a service-tier scenario (`mock_runner(...)` against a real ``blizzard hub
#: host``) speaks the same partitioned surface the real ``HttpHubClient`` does. The mock
#: hub's own routes (``blizzard_mock.mock_hub.api.routes``) moved the same way, for the
#: opposite direction (real runner against the mock hub).
_API = "/api/fleet"


def _result(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return resp.status_code, body if isinstance(body, dict) else {"body": body}


class HttpxHubGateway:
    """The mock runner's hub gateway over an injected ``httpx.Client``."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def register(self, runner_id: str, workspace_id: str) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/runners", {"runner_id": runner_id, "workspace_id": workspace_id})

    def peek(self) -> tuple[int, dict[str, Any]]:
        return self._get(f"{_API}/queue/peek")

    def claim(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/routes", body)

    def report_lease(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/events", body)

    def submit_completion(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/chunks/{chunk_id}/completions", body)

    def get_chunk(self, chunk_id: str) -> tuple[int, dict[str, Any]]:
        return self._get(f"{_API}/chunks/{chunk_id}")

    def _get(self, path: str) -> tuple[int, dict[str, Any]]:
        try:
            return _result(self._client.get(path))
        except httpx.HTTPError as exc:
            return 0, {"error": f"GET {path} failed: {exc}"}

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            return _result(self._client.post(path, json=body))
        except httpx.HTTPError as exc:
            return 0, {"error": f"POST {path} failed: {exc}"}
