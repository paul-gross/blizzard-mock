"""httpx binding for the hub-gateway seam (package-private, ``bzh:dependency-inversion``).

All httpx usage is confined here. A transport failure surfaces as ``(0, {...})`` so the
service treats an unreachable hub uniformly with a 5xx — the mock runner is a test
instrument, so it reports rather than raises.
"""

from __future__ import annotations

from typing import Any, cast

import httpx2 as httpx

from blizzard_mock.mock_runner.internal.claim_refusal import ClaimRefusal, classify_claim_refusal
from blizzard_mock.mock_runner.internal.nonclaim_refusal import classify_nonclaim_refusal

#: Every mock-runner->hub call mounts under the fleet partition,
#: mirroring the real hub's own router split.
# The release this stand-in claims to be, inside any hub window that has dropped line 0.2.
_REPORTED_VERSION = "0.4.0"
_API = "/api/fleet"


def _result(resp: httpx.Response) -> tuple[int, dict[str, Any]]:
    try:
        body: object = resp.json()
    except ValueError:
        body = {"raw": resp.text}
    return resp.status_code, cast("dict[str, Any]", body) if isinstance(body, dict) else {"body": body}


class HttpxHubGateway:
    """The mock runner's hub gateway over an injected ``httpx.Client``. Every call presents
    ``token`` as its bearer, per request, so a shared client (a test's in-process hub) can
    carry several runners' calls."""

    def __init__(self, client: httpx.Client, *, token: str | None, runner_version: str | None = None) -> None:
        self._client = client
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._runner_version = runner_version
        self.response_headers: dict[str, str] = {}
        self.claim_refusal: ClaimRefusal | None = None
        self.nonclaim_refusal: str | None = None

    def _result(self, resp: httpx.Response) -> tuple[int, dict[str, Any]]:
        # Only expose response contract headers, never credentials or arbitrary headers.
        self.response_headers = {
            name: resp.headers[name]
            for name in ("content-type", "www-authenticate", "retry-after")
            if name in resp.headers
        }
        status, body = _result(resp)
        self.nonclaim_refusal = classify_nonclaim_refusal(status, body) if status >= 400 else None
        return status, body

    def last_response_headers(self) -> dict[str, str]:
        return self.response_headers

    def last_claim_refusal(self) -> str | None:
        return self.claim_refusal

    def last_nonclaim_refusal(self) -> str | None:
        return self.nonclaim_refusal

    def add_runner(self, *, name: str) -> tuple[int, dict[str, Any]]:
        return self._post("/api/runners", {"name": name})

    def register(
        self,
        *,
        name: str,
        workspace_id: str,
        capabilities: list[dict[str, Any]] | None = None,
        subscriptions: list[dict[str, Any]] | None = None,
        declining: list[dict[str, Any]] | None = None,
        version: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        body: dict[str, Any] = {
            "name": name,
            "workspace_id": workspace_id,
            "capabilities": capabilities or [],
            "version": _REPORTED_VERSION,
        }
        if self._runner_version is not None:
            body["version"] = self._runner_version
        # `subscriptions` rides only when supplied, letting the hub default it to `None`
        # itself rather than defaulting it to `[]` here.
        if subscriptions is not None:
            body["subscriptions"] = subscriptions
        if declining is not None:
            body["declining"] = declining
        if version is not None:
            body["version"] = version
        return self._post(f"{_API}/runners", body)

    def peek(self) -> tuple[int, dict[str, Any]]:
        return self._get(f"{_API}/queue/peek")

    def peek_matched(
        self, *, enrolled: bool, capabilities: list[dict[str, Any]], policy: str
    ) -> tuple[int, dict[str, Any]]:
        path = f"{_API}/queue/peek"
        headers = self._headers if enrolled else {}
        try:
            resp = self._client.post(path, json={"capabilities": capabilities, "policy": policy}, headers=headers)
        except httpx.HTTPError as exc:
            self.response_headers = {}
            self.nonclaim_refusal = None
            return 0, {"error": f"POST {path} failed: {exc}"}
        return self._result(resp)

    def claim(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        status, response = self._post(f"{_API}/routes", body)
        self.claim_refusal = None if status == 201 else classify_claim_refusal(status, response)
        return status, response

    def submit_completion(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/chunks/{chunk_id}/completions", body)

    def get_chunk(self, chunk_id: str) -> tuple[int, dict[str, Any]]:
        return self._get(f"{_API}/chunks/{chunk_id}")

    def chunk_statuses(self, chunk_ids: list[str]) -> tuple[int, dict[str, Any]]:
        return self._get(f"{_API}/chunk-statuses", params={"chunk_id": chunk_ids})

    def submit_decision(self, chunk_id: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/chunks/{chunk_id}/decisions", body)

    def get_question(self, question_id: str) -> tuple[int, dict[str, Any]]:
        return self._get(f"{_API}/questions/{question_id}")

    def push_facts(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/events", body)

    def push_transcripts(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return self._post(f"{_API}/transcripts", body)

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
        try:
            return self._result(self._client.get(path, params=params, headers=self._headers))
        except httpx.HTTPError as exc:
            self.response_headers = {}
            self.nonclaim_refusal = None
            return 0, {"error": f"GET {path} failed: {exc}"}

    def _post(self, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        try:
            return self._result(self._client.post(path, json=body, headers=self._headers))
        except httpx.HTTPError as exc:
            self.response_headers = {}
            self.nonclaim_refusal = None
            return 0, {"error": f"POST {path} failed: {exc}"}
