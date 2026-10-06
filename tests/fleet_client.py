"""A mock-hub ``TestClient`` that speaks for a runner the way a real one does — by bearer token.

Every fleet call (``/api/fleet/...``) that carries no ``Authorization`` header presents the token
of the runner it names, adding that runner at the hub first, never connected, when the hub does
not know it yet. A test of a refused token sends its own header, or none through a plain ``TestClient``.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from fastapi.testclient import TestClient

from blizzard_mock.mock_hub.domain.service import MockHubService, RunnerNeverConnected
from blizzard_mock.mock_hub.domain.state import RunnerCapability

_FLEET = "/api/fleet/"
_RUNNER_PATH = "/api/fleet/runners/"

#: What a claim helper registers a runner with when the test registered none of its own.
DEFAULT_CLAIM_CAPABILITIES = (
    RunnerCapability(harness_id="claude_code", default=True),
    RunnerCapability(harness_id="claude", tiers=("blizzard:basic",)),
)


def runner_token(runner_id: str) -> str:
    """The deterministic bearer token a test runner holds."""
    return f"test-token-{runner_id}"


def add_runner(service: MockHubService, runner_id: str, *, name: str | None = None) -> str:
    """Add ``runner_id`` never connected unless the hub already holds it; its bearer token."""
    with contextlib.suppress(ValueError):  # already added
        service.add_runner(name=name or runner_id, runner_id=runner_id, token=runner_token(runner_id))
    return runner_token(runner_id)


def is_registered(service: MockHubService, runner_id: str) -> bool:
    """Whether ``runner_id`` was added and has registered at least once."""
    try:
        return service.runner_view(runner_id) is not None
    except RunnerNeverConnected:
        return False


def register_runner(
    service: MockHubService,
    runner_id: str,
    *,
    capabilities: tuple[RunnerCapability, ...] = DEFAULT_CLAIM_CAPABILITIES,
    workspace_id: str = "ws",
) -> None:
    """Add then register ``runner_id`` through the service, as a live runner's first tick does,
    unless it has registered already — a snapshot a test registered stays put."""
    add_runner(service, runner_id)
    if not is_registered(service, runner_id):
        service.register(runner_id, workspace_id=workspace_id, capabilities=capabilities)


class FleetClient(TestClient):
    """A ``TestClient`` presenting the named runner's bearer token on every fleet call."""

    def __init__(self, app: FastAPI, *, default_runner_id: str = "r1", **kwargs: Any) -> None:
        super().__init__(app, **kwargs)
        self.default_runner_id = default_runner_id

    @property
    def service(self) -> MockHubService:
        service: MockHubService = self.app.state.service  # type: ignore[attr-defined]
        return service

    def request(self, method: str, url: Any, **kwargs: Any) -> Any:  # type: ignore[override]
        path = urlsplit(str(url)).path
        headers = dict(kwargs.pop("headers", None) or {})
        if path.startswith(_FLEET) and not any(key.lower() == "authorization" for key in headers):
            headers["Authorization"] = f"Bearer {add_runner(self.service, self._caller(str(url), kwargs))}"
        return super().request(method, url, headers=headers or None, **kwargs)

    def _caller(self, url: str, kwargs: dict[str, Any]) -> str:
        path = urlsplit(url).path
        body = kwargs.get("json")
        if body is None and isinstance(kwargs.get("content"), str | bytes):
            try:
                body = json.loads(kwargs["content"])
            except ValueError:
                body = None
        if isinstance(body, dict) and isinstance(body.get("runner_id"), str):
            return body["runner_id"]
        if path.startswith(_RUNNER_PATH):
            return path[len(_RUNNER_PATH) :].split("/", 1)[0]
        params = kwargs.get("params")
        if isinstance(params, dict) and isinstance(params.get("runner_id"), str):
            return params["runner_id"]
        query = parse_qs(urlsplit(url).query)
        if query.get("runner_id"):
            return query["runner_id"][0]
        return self.default_runner_id
