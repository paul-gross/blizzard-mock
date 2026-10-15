"""``blizzard-mock-fixture`` — mint / destroy / reset / locate a fixture workspace.

The composition root: resolves scratch root, env, and local winter source from
flags / environment (``--env``, ``--scratch-root``, ``--winter-source``) and
wires the adapters into :class:`FixtureWorkspaceService`. The fixture is
winter-free unless a winter source is named explicitly; the winter adapter is
constructed only then.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import click

from blizzard_mock.fixture_workspace.errors import FixtureError
from blizzard_mock.fixture_workspace.internal.subprocess_git import SubprocessGit
from blizzard_mock.fixture_workspace.internal.subprocess_winter import SubprocessWinterCli
from blizzard_mock.fixture_workspace.service import FixtureWorkspaceService

_ENV_SCRATCH = "BLIZZARD_MOCK_SCRATCH_ROOT"
_ENV_WINTER = "BLIZZARD_MOCK_WINTER_SOURCE"
_ENV_WINTER_ENV = "WINTER_ENV"


def _resolve_env(explicit: str | None) -> str:
    env = explicit or os.environ.get(_ENV_WINTER_ENV)
    if not env:
        raise click.ClickException(f"no env: pass --env or set ${_ENV_WINTER_ENV}")
    return env


def _resolve_scratch_root(explicit: str | None) -> Path:
    raw = explicit or os.environ.get(_ENV_SCRATCH)
    if raw:
        return Path(raw).expanduser().resolve()
    return Path(tempfile.gettempdir()) / "blizzard-mock" / "fixtures"


def _resolve_winter_source(explicit: str | None) -> Path | None:
    raw = explicit or os.environ.get(_ENV_WINTER)
    if raw:
        return Path(raw).expanduser().resolve()
    return None


def _service(scratch_root: str | None, winter_source: str | None) -> FixtureWorkspaceService:
    source = _resolve_winter_source(winter_source)
    return FixtureWorkspaceService(
        git=SubprocessGit(),
        winter=SubprocessWinterCli() if source is not None else None,
        scratch_root=_resolve_scratch_root(scratch_root),
        winter_source=source,
    )


_env_opt = click.option("--env", "env_", default=None, help="Feature env keying the fixture (default $WINTER_ENV).")
_scratch_opt = click.option("--scratch-root", default=None, help=f"Fixtures base dir (default ${_ENV_SCRATCH}).")
_winter_opt = click.option(
    "--winter-source",
    default=None,
    help=f"Local winter workspace to clone (default ${_ENV_WINTER}; none gives a winter-free fixture).",
)


@click.group()
@click.version_option(package_name="blizzard-mock")
def main() -> None:
    """Mint and manage disposable fixture workspaces (winter-free unless a winter source is given)."""


@main.command()
@_env_opt
@_scratch_opt
@_winter_opt
def mint(env_: str | None, scratch_root: str | None, winter_source: str | None) -> None:
    """Mint a fresh fixture workspace (bare origins; with a winter source, also a real winter workspace + ws init)."""
    try:
        layout = _service(scratch_root, winter_source).mint(_resolve_env(env_))
    except FixtureError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(layout.workspace))


@main.command()
@_env_opt
@_scratch_opt
def destroy(env_: str | None, scratch_root: str | None) -> None:
    """Remove the fixture workspace for the env."""
    try:
        removed = _service(scratch_root, None).destroy(_resolve_env(env_))
    except FixtureError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("destroyed" if removed else "nothing to destroy")


@main.command()
@_env_opt
@_scratch_opt
@_winter_opt
def reset(env_: str | None, scratch_root: str | None, winter_source: str | None) -> None:
    """Re-mint the fixture from clean (destroy if present, then mint)."""
    try:
        layout = _service(scratch_root, winter_source).reset(_resolve_env(env_))
    except FixtureError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(str(layout.workspace))


@main.command()
@_env_opt
@_scratch_opt
@click.option(
    "--part",
    type=click.Choice(["workspace", "origins", "root"]),
    default="workspace",
    help="Which path to print: the winter workspace root, the bare-origins dir, or the fixture root.",
)
def path(env_: str | None, scratch_root: str | None, part: str) -> None:
    """Print a fixture path (workspace root by default; origins for the mock forge)."""
    layout = _service(scratch_root, None).layout(_resolve_env(env_))
    click.echo(str(getattr(layout, part)))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
