"""Entrypoint for the mock runner driver (``blizzard-mock-runner``).

Resolves its bind address and the hub it drives, then serves the driver's
control API with uvicorn. A hub service-tier test arms this driver's levers
and POSTs ``/_drive/*`` to exercise the real hub's API over the wire.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import click

from blizzard_mock.mock_runner.app import create_app
from blizzard_mock.mock_runner.config import MockRunnerConfig
from blizzard_mock.serve import listening_file_option, serve


@click.command()
@click.option("--host", envvar="BZ_MOCK_RUNNER_HOST", default=None, help="Bind host for the control API.")
@click.option("--port", envvar="BZ_MOCK_RUNNER_PORT", type=int, default=None, help="Bind port for the control API.")
@click.option("--hub-url", envvar="BZ_HUB_URL", default=None, help="The hub the driver targets.")
@click.option(
    "--token",
    envvar="BZ_HUB_TOKEN",
    default=None,
    help="The bearer token the driver presents; absent, it adds itself at the hub on start.",
)
@click.option("--name", envvar="BZ_MOCK_RUNNER_NAME", default=None, help="The name the driver registers under.")
@listening_file_option
def main(
    host: str | None,
    port: int | None,
    hub_url: str | None,
    token: str | None,
    name: str | None,
    listening_file: Path | None,
) -> None:
    """Serve the mock runner — a levered driver of the runner's outbound protocol."""
    config = MockRunnerConfig.from_env(host=host, port=port, hub_url=hub_url, token=token, name=name)
    serve(
        lambda bound: create_app(replace(config, port=bound)),
        host=config.host,
        port=config.port,
        listening_file=listening_file,
    )


if __name__ == "__main__":
    main()
