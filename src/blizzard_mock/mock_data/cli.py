"""The mock-data CLI (``blizzard-mock-data``).

Skeleton (bootstrap P4 item 4): the click group and its verbs are real and their
``--help`` describes the intended contract, but each verb raises a clear "not
implemented" message because the domain models it operates on do not exist until
the hub/runner are scaffolded (P5). The surface is stable; the Build step fills
the bodies verb-by-verb as those models land.

Contract (``implementation/mocking.md``):

- operates on **domain models, not raw tables**;
- fixtures are **named, versioned scenarios**;
- **reset** returns a store to a known-clean state.
"""

from __future__ import annotations

import click

_STORE_CHOICES = click.Choice(["hub", "runner"])


def _not_implemented(what: str) -> None:
    """Raise a clean, actionable "not implemented" error (exit 1, no traceback).

    Used while this CLI is a skeleton: the verb, its help, and its options are
    real, but the body waits on domain models that do not exist yet.
    """
    raise click.ClickException(
        f"{what} is not implemented yet — the mock-data CLI is a skeleton and grows "
        "alongside the hub/runner domain models it operates on (bootstrap P5). "
        "See src/blizzard_mock/mock_data/README.md."
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="blizzard-mock")
def cli() -> None:
    """Seed, reset, and fixture the hub/runner stores for repeatable test cases.

    A tool *for agents*: it operates on domain models, not raw tables — ask for
    "a chunk parked on a question", not for rows. Larger worlds come from named,
    versioned fixtures shared by the suite and agents. Reset returns a store to
    a known-clean state so every run starts from the same ground.
    """


@cli.command()
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to reset.")
@click.option(
    "--url",
    "url",
    envvar="DATABASE_URL",
    default=None,
    help="Target store URL (sqlite path or postgres DATABASE_URL). Defaults to $DATABASE_URL.",
)
def reset(store: str, url: str | None) -> None:
    """Return a store to a known-clean state.

    Drops all mock-seeded domain state so the next test run starts from the same
    ground. Operates on domain models, not raw tables.
    """
    _not_implemented(f"reset (store={store!r})")


@cli.command()
@click.argument("model")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option(
    "--url",
    "url",
    envvar="DATABASE_URL",
    default=None,
    help="Target store URL (sqlite path or postgres DATABASE_URL). Defaults to $DATABASE_URL.",
)
def create(model: str, store: str, url: str | None) -> None:
    """Create one domain-model instance (e.g. a chunk parked on a question).

    MODEL names the domain concept to instantiate — not a table. The available
    models grow as the hub/runner domain lands.
    """
    _not_implemented(f"create (model={model!r}, store={store!r})")


@cli.group()
def fixture() -> None:
    """Work with named, versioned fixture scenarios.

    A fixture is a named, versioned scenario the suite and agents share; applying
    one mints a *consistent world* across the store rows, the mock-forge state,
    and the fixture-workspace git state together.
    """


@fixture.command("list")
def fixture_list() -> None:
    """List the available named fixture scenarios and their versions."""
    _not_implemented("fixture list")


@fixture.command("apply")
@click.argument("name")
@click.option("--version", "version", default=None, help="Pin a fixture version (defaults to latest).")
def fixture_apply(name: str, version: str | None) -> None:
    """Apply the named fixture scenario, minting its consistent world.

    NAME selects the scenario; --version pins it. Applying seeds the store rows
    and coordinates the matching forge state and fixture-workspace git state so
    all three state holders agree.
    """
    _not_implemented(f"fixture apply (name={name!r}, version={version!r})")
