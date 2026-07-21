"""Entrypoint for the stub IdP service (``blizzard-mock-idp``).

Resolves config (``--host`` / ``BZ_IDP_HOST``, ``--port`` / ``BZ_IDP_PORT``) and serves
the stub-IdP FastAPI app with uvicorn.
"""

from __future__ import annotations

import click
import uvicorn

from blizzard_mock.idp.app import create_app
from blizzard_mock.idp.config import IdpConfig


@click.command()
@click.option("--host", envvar="BZ_IDP_HOST", default=None, help="Bind host.")
@click.option("--port", envvar="BZ_IDP_PORT", type=int, default=None, help="Bind port.")
def main(host: str | None, port: int | None) -> None:
    """Serve the stub OAuth identity provider (OIDC + GitHub-style)."""
    config = IdpConfig.from_env(host=host, port=port)
    app = create_app()
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
