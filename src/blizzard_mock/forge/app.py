"""Composition root — wire the forge and build the FastAPI app.

The single place collaborators are constructed (``bzh:dependency-injection``):
the GitPython backend, the in-memory state and lever stores, the clock, and the
``ForgeService`` are bound here and stashed on ``app.state`` for the routers.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from blizzard_mock.clock import ControlledClock, resolve_clock
from blizzard_mock.clock_api import clock_router
from blizzard_mock.forge.api import serialization as ser
from blizzard_mock.forge.api.apps import router as apps_router
from blizzard_mock.forge.api.graphql import router as graphql_router
from blizzard_mock.forge.api.issues import router as issues_router
from blizzard_mock.forge.api.labels import router as labels_router
from blizzard_mock.forge.api.levers import router as levers_router
from blizzard_mock.forge.api.middleware import LeverMiddleware, RateHeaderMiddleware, RequestIdMiddleware
from blizzard_mock.forge.api.pulls import router as pulls_router
from blizzard_mock.forge.api.refs import router as refs_router
from blizzard_mock.forge.api.repos import router as repos_router
from blizzard_mock.forge.api.web import router as web_router
from blizzard_mock.forge.config import ForgeConfig
from blizzard_mock.forge.domain.app_service import AppService
from blizzard_mock.forge.domain.clock import Clock
from blizzard_mock.forge.domain.errors import ForgeError
from blizzard_mock.forge.domain.service import ForgeService
from blizzard_mock.forge.internal.app_registry import InMemoryAppRegistry
from blizzard_mock.forge.internal.check_store import InMemoryCheckRunStore
from blizzard_mock.forge.internal.errors import GitErrorFactory
from blizzard_mock.forge.internal.git_backend import GitBackend
from blizzard_mock.forge.internal.lever_store import InMemoryLeverStore
from blizzard_mock.forge.internal.rate_ledger import InMemoryRateLedger
from blizzard_mock.forge.internal.state_store import InMemoryForgeState
from blizzard_mock.forge.internal.status_store import InMemoryStatusStore


def create_app(config: ForgeConfig, *, clock: Clock | None = None) -> FastAPI:
    """Build a fully wired forge app from resolved config."""
    log = structlog.get_logger("blizzard_mock.forge")
    the_clock = clock if clock is not None else resolve_clock()

    git = GitBackend(config.repos_dir, GitErrorFactory(log))
    state = InMemoryForgeState()
    levers = InMemoryLeverStore()
    checks = InMemoryCheckRunStore()
    rate = InMemoryRateLedger(the_clock)
    apps = AppService(InMemoryAppRegistry(), levers, the_clock)
    service = ForgeService(git, state, levers, the_clock, checks, rate, apps, InMemoryStatusStore())

    app = FastAPI(title="blizzard-mock forge", version="0.1.0")
    if isinstance(the_clock, ControlledClock):
        app.include_router(clock_router(the_clock))
    app.state.service = service
    app.state.levers = levers
    app.state.apps = apps
    app.state.base_url = config.base_url

    # Added first, so it sits inside LeverMiddleware: a lever refusal is never counted or hashed.
    app.add_middleware(RateHeaderMiddleware, rate=rate)
    app.add_middleware(LeverMiddleware, levers=levers, rate=rate, apps=apps)
    # Added last, so it is outermost and stamps every answer.
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(ForgeError)
    async def _forge_error_handler(_request: Request, exc: ForgeError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=ser.error_json(exc.message), headers=exc.headers)

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(repos_router)
    app.include_router(issues_router)
    app.include_router(apps_router)
    app.include_router(graphql_router)
    app.include_router(labels_router)
    app.include_router(pulls_router)
    app.include_router(refs_router)
    app.include_router(levers_router)
    app.include_router(web_router)

    log.info("forge app created", repos_dir=str(config.repos_dir), base_url=config.base_url)
    return app
