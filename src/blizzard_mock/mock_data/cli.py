"""The mock-data CLI (``blizzard-mock-data``).

A tool *for agents* to set up test cases repeatably against the **real** hub/runner
stores (sqlite or postgres) the service tier and crash sweep run over
(``implementation/mocking.md``). It operates on **domain models, not raw tables**, and —
crucially — **without importing ``blizzard``**: it **reflects** the live store's schema at
runtime (SQLAlchemy), so it works against whatever the daemon's Alembic tree migrated,
and a mock-repo edit never has to chase a hub/runner schema change.

Implemented verbs (bootstrap P7W4, the service tier's seeding needs):

- ``reset --store hub|runner`` — return a store to a **known-clean** state: delete every
  row from every table in FK-safe order. The workhorse — every service scenario starts
  from clean ground.
- ``create runner --store hub --runner-id R`` — seed one registered runner into the hub's
  fleet registry (``--paused`` also lands a pause fact). A simple, self-contained domain
  row the board and the runner's pause-readback observe.

Richer domain rows (a chunk, a graph, a parked question) are seeded through the daemons'
own HTTP APIs in the service tier, which self-validate — so the mock-data ``create`` is
deliberately thin here. ``create <other>`` and the ``fixture`` subgroup remain stubs.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click
from sqlalchemy import MetaData, create_engine, delete, insert

_STORE_CHOICES = click.Choice(["hub", "runner"])


def _resolve_engine(url: str | None):
    if not url:
        raise click.UsageError("no store URL — pass --url or set DATABASE_URL (sqlite path or postgres DSN)")
    return create_engine(url)


def _reflect(engine) -> MetaData:
    meta = MetaData()
    meta.reflect(bind=engine)
    if not meta.tables:
        raise click.ClickException("the store has no tables — is it migrated? (run the daemon's `migrate`)")
    return meta


def _not_implemented(what: str) -> None:
    raise click.ClickException(
        f"{what} is not implemented — richer domain rows seed through the daemon's own HTTP API "
        "in the service tier (self-validating). See src/blizzard_mock/mock_data/README.md."
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="blizzard-mock")
def cli() -> None:
    """Seed, reset, and fixture the hub/runner stores for repeatable test cases.

    A tool *for agents*: it operates on domain models, not raw tables, and reflects the
    live store's schema so it never imports ``blizzard``. Reset returns a store to a
    known-clean state so every run starts from the same ground.
    """


@cli.command()
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to reset (labels the target).")
@click.option(
    "--url",
    "url",
    envvar="DATABASE_URL",
    default=None,
    help="Store URL (sqlite path or postgres DSN). Defaults to $DATABASE_URL.",
)
def reset(store: str, url: str | None) -> None:
    """Return a store to a known-clean state — delete every row, FK-safe.

    Reflects the live schema and deletes from every table in reverse dependency order, so
    the next test run starts from the same ground. The tables (the facts-only schema)
    survive; only the rows go.
    """
    engine = _resolve_engine(url)
    meta = _reflect(engine)
    deleted = 0
    with engine.begin() as conn:
        for table in reversed(meta.sorted_tables):  # children before parents (FK-safe)
            deleted += conn.execute(delete(table)).rowcount or 0
    click.echo(f"reset {store} store: cleared {deleted} row(s) across {len(meta.tables)} table(s)")


@cli.command()
@click.argument("model")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option(
    "--url",
    "url",
    envvar="DATABASE_URL",
    default=None,
    help="Store URL (sqlite path or postgres DSN). Defaults to $DATABASE_URL.",
)
@click.option("--runner-id", "runner_id", default="runner-seed", help="The runner id (model=runner).")
@click.option("--workspace-id", "workspace_id", default="workspace-seed", help="The workspace binding (model=runner).")
@click.option("--paused", is_flag=True, default=False, help="Also land a pause fact (model=runner).")
def create(model: str, store: str, url: str | None, runner_id: str, workspace_id: str, paused: bool) -> None:
    """Create one domain-model instance (e.g. ``runner`` — a registered fleet runner).

    MODEL names the domain concept, not a table. ``runner`` seeds the hub's fleet registry
    (and, with ``--paused``, a pause fact the runner reads back on its pull).
    """
    if model != "runner":
        _not_implemented(f"create (model={model!r})")
    if store != "hub":
        raise click.UsageError("model 'runner' lives in the hub store (--store hub)")
    engine = _resolve_engine(url)
    meta = _reflect(engine)
    registrations = meta.tables.get("runner_registrations")
    if registrations is None:
        raise click.ClickException("hub store has no runner_registrations table — is it the migrated hub store?")
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(registrations).values(
                runner_id=runner_id, workspace_id=workspace_id, registered_at=now, last_seen_at=now
            )
        )
        if paused:
            pause_facts = meta.tables.get("runner_pause_facts")
            if pause_facts is None:
                raise click.ClickException("hub store has no runner_pause_facts table")
            conn.execute(insert(pause_facts).values(runner_id=runner_id, paused=True, set_at=now, set_by="mock-data"))
    click.echo(f"created runner {runner_id!r} in the hub store (paused={paused})")


@cli.group()
def fixture() -> None:
    """Work with named, versioned fixture scenarios (stub — see the module docstring)."""


@fixture.command("list")
def fixture_list() -> None:
    """List the available named fixture scenarios and their versions."""
    _not_implemented("fixture list")


@fixture.command("apply")
@click.argument("name")
@click.option("--version", "version", default=None, help="Pin a fixture version (defaults to latest).")
def fixture_apply(name: str, version: str | None) -> None:
    """Apply the named fixture scenario, minting its consistent world."""
    _not_implemented(f"fixture apply (name={name!r}, version={version!r})")
