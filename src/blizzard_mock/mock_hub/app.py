"""Composition root — wire the mock hub and build its FastAPI app (``bzh:dependency-injection``).

The single place collaborators are constructed: the in-memory state, the shared lever
store, the clock, and the ``MockHubService``, bound here and stashed on ``app.state`` for
the routers. Tests call ``create_app`` with a ``FixedClock``; the CLI calls it from
resolved config. A ``ChunkNotFound`` bubbles to a 404 handler.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from blizzard_mock.clock import Clock, SystemClock
from blizzard_mock.levers import InMemoryLeverStore
from blizzard_mock.mock_hub.api.control import captured_router, levers_router, seed_router
from blizzard_mock.mock_hub.api.middleware import HubLeverMiddleware, RequestCaptureMiddleware
from blizzard_mock.mock_hub.api.routes import fleet_router as hub_fleet_router
from blizzard_mock.mock_hub.api.routes import router as hub_router
from blizzard_mock.mock_hub.config import MockHubConfig
from blizzard_mock.mock_hub.domain.capture import InMemoryCaptureStore
from blizzard_mock.mock_hub.domain.service import ChunkNotFound, MockHubService
from blizzard_mock.mock_hub.internal.state_store import InMemoryHubState


def create_app(config: MockHubConfig | None = None, *, clock: Clock | None = None) -> FastAPI:
    """Build a fully wired mock-hub app."""
    log = structlog.get_logger("blizzard_mock.mock_hub")
    the_clock = clock or SystemClock()

    state = InMemoryHubState()
    levers = InMemoryLeverStore()
    captured = InMemoryCaptureStore()
    service = MockHubService(state, levers, the_clock)

    app = FastAPI(title="blizzard-mock hub", version="0.1.0")
    app.state.service = service
    app.state.levers = levers
    app.state.captured = captured

    app.add_middleware(HubLeverMiddleware, levers=levers)
    app.add_middleware(RequestCaptureMiddleware, captured=captured)

    @app.exception_handler(ChunkNotFound)
    async def _not_found(_request: Request, exc: ChunkNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    app.include_router(hub_router)
    app.include_router(hub_fleet_router)
    app.include_router(seed_router)
    app.include_router(levers_router)
    app.include_router(captured_router)

    log.info("mock hub app created")
    return app
