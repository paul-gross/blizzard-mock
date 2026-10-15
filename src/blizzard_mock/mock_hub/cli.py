"""Entrypoint for the mock hub service (``blizzard-mock-hub``).

Resolves the bind address (``--host`` / ``BZ_MOCK_HUB_HOST``, ``--port`` /
``BZ_MOCK_HUB_PORT``) and the host-plane-absent switch (``--no-host-plane`` /
``BZ_MOCK_HUB_NO_HOST_PLANE``), and serves the mock-hub FastAPI app with uvicorn. A service-tier
test that runs the real runner out of process points ``BZ_HUB_URL`` at this address.
"""

from __future__ import annotations

import click
import uvicorn

from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.config import MockHubConfig


@click.command()
@click.option("--host", envvar="BZ_MOCK_HUB_HOST", default=None, help="Bind host.")
@click.option("--port", envvar="BZ_MOCK_HUB_PORT", type=int, default=None, help="Bind port.")
@click.option(
    "--no-host-plane",
    is_flag=True,
    default=None,
    help="Answer every /api/fleet/host route 404 — a hub from before hosts (env BZ_MOCK_HUB_NO_HOST_PLANE=1).",
)
def main(host: str | None, port: int | None, no_host_plane: bool | None) -> None:
    """Serve the mock hub — a stateful stand-in for the hub API a runner consumes."""
    config = MockHubConfig.from_env(host=host, port=port, host_plane_absent=no_host_plane or None)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
