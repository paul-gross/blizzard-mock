"""Entrypoint for the mock GitHub forge service (``blizzard-mock-forge``).

Resolves config (``--repos-dir`` / ``BZ_FORGE_REPOS_DIR``, ``--host`` /
``BZ_FORGE_HOST``, ``--port`` / ``BZ_FORGE_PORT``) and serves the forge FastAPI
app with uvicorn, bound to the winter service band.
"""

from __future__ import annotations

import click
import uvicorn

from blizzard_mock.forge.app import create_app
from blizzard_mock.forge.config import ForgeConfig


@click.command()
@click.option(
    "--repos-dir",
    envvar="BZ_FORGE_REPOS_DIR",
    default=None,
    help="Directory of bare git repos the forge fronts.",
)
@click.option("--host", envvar="BZ_FORGE_HOST", default=None, help="Bind host.")
@click.option("--port", envvar="BZ_FORGE_PORT", type=int, default=None, help="Bind port.")
def main(repos_dir: str | None, host: str | None, port: int | None) -> None:
    """Serve the mock GitHub forge over a directory of bare git repos."""
    config = ForgeConfig.from_env(repos_dir=repos_dir, host=host, port=port)
    app = create_app(config)
    uvicorn.run(app, host=config.host, port=config.port)


if __name__ == "__main__":
    main()
