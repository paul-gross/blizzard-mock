"""Composition root — wire the mock runner and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed: the httpx client to the hub, the httpx
gateway over it, the shared lever store, the clock, and the ``MockRunnerService``. The
client is created here and closed on shutdown so no other code touches httpx. Tests call
``create_app`` with an injected gateway (an in-process mock hub over ``ASGITransport``);
the CLI builds a real ``httpx.Client`` from ``hub_url``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import structlog
from fastapi import FastAPI

from blizzard_mock.clock import Clock, SystemClock
from blizzard_mock.levers import InMemoryLeverStore
from blizzard_mock.mock_runner.api.routes import api_router, drive_router, levers_router
from blizzard_mock.mock_runner.config import MockRunnerConfig
from blizzard_mock.mock_runner.domain.gateway import IHubGateway
from blizzard_mock.mock_runner.domain.service import MockRunnerService
from blizzard_mock.mock_runner.internal.httpx_gateway import HttpxHubGateway


def create_app(
    config: MockRunnerConfig | None = None,
    *,
    gateway: IHubGateway | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """Build a fully wired mock-runner app.

    ``gateway`` is injected by tests (an in-process mock hub); the CLI passes ``None`` and
    an ``httpx.Client`` to ``config.hub_url`` is opened here and closed on shutdown.
    """
    cfg = config or MockRunnerConfig()
    log = structlog.get_logger("blizzard_mock.mock_runner")
    the_clock = clock or SystemClock()
    levers = InMemoryLeverStore()

    owned_client: httpx.Client | None = None
    if gateway is None:
        owned_client = httpx.Client(base_url=cfg.hub_url, timeout=30.0)
        gateway = HttpxHubGateway(owned_client)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if owned_client is not None:
                owned_client.close()

    app = FastAPI(title="blizzard-mock runner", version="0.1.0", lifespan=_lifespan)
    service = MockRunnerService(gateway, levers, the_clock, runner_id=cfg.runner_id, workspace_id=cfg.workspace_id)
    app.state.service = service
    app.state.levers = levers

    app.include_router(api_router)
    app.include_router(drive_router)
    app.include_router(levers_router)

    log.info("mock runner app created", hub_url=cfg.hub_url, runner_id=cfg.runner_id)
    return app
