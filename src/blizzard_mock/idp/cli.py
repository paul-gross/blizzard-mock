"""Entrypoint for the stub IdP service (``blizzard-mock-idp``).

Resolves config (``--host`` / ``BZ_IDP_HOST``, ``--port`` / ``BZ_IDP_PORT``) and serves
the stub-IdP FastAPI app with uvicorn.
"""

from __future__ import annotations

from pathlib import Path

import click

from blizzard_mock.idp.app import create_app
from blizzard_mock.idp.config import IdpConfig
from blizzard_mock.serve import serve


@click.command()
@click.option("--host", envvar="BZ_IDP_HOST", default=None, help="Bind host.")
@click.option("--port", envvar="BZ_IDP_PORT", type=int, default=None, help="Bind port.")
@click.option(
    "--listening-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Once serving, write http://<host>:<bound-port> here (atomically); pairs with --port 0.",
)
def main(host: str | None, port: int | None, listening_file: Path | None) -> None:
    """Serve the stub OAuth identity provider (OIDC + GitHub-style)."""
    config = IdpConfig.from_env(host=host, port=port)
    serve(lambda _bound: create_app(), host=config.host, port=config.port, listening_file=listening_file)


if __name__ == "__main__":
    main()
