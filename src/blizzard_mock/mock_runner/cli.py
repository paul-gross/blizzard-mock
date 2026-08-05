"""Entrypoint for the mock runner driver (``blizzard-mock-runner``).

Resolves its bind address and the hub it drives, then serves the driver's
control API with uvicorn. A hub service-tier test arms this driver's levers
and POSTs ``/_drive/*`` to exercise the real hub's API over the wire.
"""

from __future__ import annotations

import click
import uvicorn

from blizzard_mock.mock_runner.app import create_app
from blizzard_mock.mock_runner.config import MockRunnerConfig


@click.command()
@click.option("--host", envvar="BZ_MOCK_RUNNER_HOST", default=None, help="Bind host for the control API.")
@click.option("--port", envvar="BZ_MOCK_RUNNER_PORT", type=int, default=None, help="Bind port for the control API.")
@click.option("--hub-url", envvar="BZ_HUB_URL", default=None, help="The hub the driver targets.")
@click.option("--runner-id", envvar="BZ_MOCK_RUNNER_ID", default=None, help="The runner id the driver claims as.")
def main(host: str | None, port: int | None, hub_url: str | None, runner_id: str | None) -> None:
    """Serve the mock runner — a levered driver of the runner's outbound protocol."""
    config = MockRunnerConfig.from_env(host=host, port=port, hub_url=hub_url, runner_id=runner_id)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
