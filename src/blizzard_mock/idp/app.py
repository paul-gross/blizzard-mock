"""Composition root — wire the stub IdP and build the FastAPI app.

Mirrors ``blizzard_mock.forge.app.create_app``'s shape: the in-memory state and the
RS256 signer are constructed here and stashed on ``app.state`` for the routers.
"""

from __future__ import annotations

from fastapi import FastAPI

from blizzard_mock.idp.api.github import router as github_router
from blizzard_mock.idp.api.levers import router as levers_router
from blizzard_mock.idp.api.oidc import router as oidc_router
from blizzard_mock.idp.domain.signing import IdTokenSigner
from blizzard_mock.idp.domain.state import IdpState


def create_app() -> FastAPI:
    """Build a fully wired stub-IdP app — one process, one keypair, one profile."""
    app = FastAPI(title="blizzard-mock idp", version="0.1.0")
    app.state.idp_state = IdpState()
    app.state.signer = IdTokenSigner()

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(oidc_router)
    app.include_router(github_router)
    app.include_router(levers_router)
    return app
