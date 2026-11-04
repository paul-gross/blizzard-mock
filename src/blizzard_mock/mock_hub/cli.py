"""Entrypoint for the mock hub service (``blizzard-mock-hub``).

Resolves the bind address (``--host`` / ``BZ_MOCK_HUB_HOST``, ``--port`` /
``BZ_MOCK_HUB_PORT``) and the host-plane-absent switch (``--no-host-plane`` /
``BZ_MOCK_HUB_NO_HOST_PLANE``) and IdP-offered (``--offer-idp``) switches, and serves the mock-hub FastAPI app
with uvicorn. A service-tier test that runs the real runner out of process points ``BZ_HUB_URL`` here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import click

from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.config import MockHubConfig
from blizzard_mock.serve import serve


@click.command()
@click.option("--host", envvar="BZ_MOCK_HUB_HOST", default=None, help="Bind host.")
@click.option("--port", envvar="BZ_MOCK_HUB_PORT", type=int, default=None, help="Bind port.")
@click.option(
    "--no-host-plane",
    is_flag=True,
    default=None,
    help="Answer every /api/fleet/host route 404 — a hub from before hosts (env BZ_MOCK_HUB_NO_HOST_PLANE=1).",
)
@click.option(
    "--offer-idp",
    is_flag=True,
    default=None,
    help='Serve GET /api/auth/jwks.json as 200 {"keys": []} — a hub offering an IdP (env BZ_MOCK_HUB_OFFER_IDP=1).',
)
@click.option(
    "--listening-file",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Once serving, write http://<host>:<bound-port> here (atomically); pairs with --port 0.",
)
def main(
    host: str | None,
    port: int | None,
    no_host_plane: bool | None,
    offer_idp: bool | None,
    listening_file: Path | None,
) -> None:
    """Serve the mock hub — a stateful stand-in for the hub API a runner consumes."""
    config = MockHubConfig.from_env(
        host=host, port=port, host_plane_absent=no_host_plane or None, idp_offered=offer_idp or None
    )
    serve(
        lambda bound: create_app(replace(config, port=bound)),
        host=config.host,
        port=config.port,
        listening_file=listening_file,
    )


if __name__ == "__main__":
    main()
