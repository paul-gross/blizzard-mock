"""Entrypoint for the mock GitHub forge service (``blizzard-mock-forge``).

Resolves config (``--repos-dir`` / ``BZ_FORGE_REPOS_DIR``, ``--host`` /
``BZ_FORGE_HOST``, ``--port`` / ``BZ_FORGE_PORT``) and serves the forge FastAPI
app with uvicorn, bound to the winter service band.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import click

from blizzard_mock.forge.app import create_app
from blizzard_mock.forge.config import ForgeConfig
from blizzard_mock.serve import listening_file_option, serve


@click.command()
@click.option(
    "--repos-dir",
    envvar="BZ_FORGE_REPOS_DIR",
    default=None,
    help="Directory of bare git repos the forge fronts.",
)
@click.option("--host", envvar="BZ_FORGE_HOST", default=None, help="Bind host.")
@click.option("--port", envvar="BZ_FORGE_PORT", type=int, default=None, help="Bind port.")
@listening_file_option
def main(repos_dir: str | None, host: str | None, port: int | None, listening_file: Path | None) -> None:
    """Serve the mock GitHub forge over a directory of bare git repos."""
    try:
        config = ForgeConfig.from_env(repos_dir=repos_dir, host=host, port=port)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc
    serve(
        lambda bound: create_app(replace(config, port=bound)),
        host=config.host,
        port=config.port,
        listening_file=listening_file,
    )


if __name__ == "__main__":
    main()
