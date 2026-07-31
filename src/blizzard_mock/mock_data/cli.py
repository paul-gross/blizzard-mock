"""The mock-data CLI (``blizzard-mock-data``).

A tool *for agents* to set up test cases repeatably against the **real** hub/runner
stores (sqlite or postgres) the service tier and crash sweep run over
(``implementation/mocking.md``). It operates on **domain models, not raw tables**, and —
crucially — **without importing ``blizzard``**: it **reflects** the live store's schema at
runtime (SQLAlchemy), so it works against whatever the daemon's Alembic tree migrated,
and a mock-repo edit never has to chase a hub/runner schema change.

This module is the composition root (``bzh:dependency-injection``): it parses flags,
resolves a store URL, wires the domain seam (``domain/seeding.SeedService`` over the
``internal/reflected_store.ReflectedStore`` adapter) with a real clock, calls it, and
echoes the result — no SQLAlchemy or store logic lives here.

Implemented verbs (bootstrap P7W4 + P2, the service tier's seeding needs):

- ``reset --store hub|runner`` — return a store to a **known-clean** state: delete every
  row from every table in FK-safe order. The workhorse — every service scenario starts
  from clean ground.
- ``create`` is a **group** — one subcommand per domain concept:

  - ``create runner --store hub --runner-id R`` — seed one registered runner into the
    hub's fleet registry (``--paused`` also lands a pause fact). A simple,
    self-contained domain row the board and the runner's pause-readback observe.
  - ``create graph [--name NAME]`` — mint a synthetic workflow graph (``domain/
    graph_seed.py``): a ``build`` (runner) node and a ``deliver`` (hub) node, so a
    later ``create chunk --status delivering`` has a hub node to transition into. A
    freshly provisioned hub mints no graph of its own (the real default graph is
    minted lazily, on first ingest), which is exactly why this exists.
  - ``create chunk --status <status>`` — the root verb: composes and writes the exact
    fact rows the hub's ``derive_chunk_status`` reads to arrive at ``status``
    (``domain/chunk_seed.py``, ``bzh:facts-not-status``), never a status column.
    Auto-mints a graph when the store has none, or reuses one by ``--graph <name>``.
    Prints the minted chunk id, alone, on stdout — pipeable into a sibling verb.

Every verb accepts a target store as ``--url``/``$DATABASE_URL`` or as ``--dir`` — a hub/
runner runtime directory whose ``blizzard-hub.toml``/``blizzard-runner.toml`` names the
``db_url`` (``internal/hub_runtime.py``) — sugar for ``--url`` that resolves before the
same code path runs.

Every write runs the drift guard (``domain/schema_contract.py``) first: a schema drift —
the live store has moved out from under this tool — fails loud, naming the table and
column(s), never a silently-wrong row.

Richer domain rows beyond a runner/graph/chunk (a parked question's *answer*, a usage
event, a runner-pause fact) are seeded through the daemons' own HTTP APIs in the
service tier, which self-validate, or land in a later phase. The ``fixture`` subgroup
remains a stub.
"""

from __future__ import annotations

import random
from pathlib import Path

import click

from blizzard_mock.clock import Clock, SystemClock
from blizzard_mock.mock_data.domain.chunk_seed import STATUSES, ChunkCompositionError, compose_chunk
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.graph_seed import (
    DEFAULT_GRAPH_NAME,
    GraphCompositionError,
    GraphContext,
    compose_graph,
    hydrate_graph_context,
)
from blizzard_mock.mock_data.domain.ids import seeded_rng
from blizzard_mock.mock_data.domain.schema_contract import SchemaDriftError
from blizzard_mock.mock_data.domain.seeding import SeedService
from blizzard_mock.mock_data.internal.hub_runtime import HubRuntimeError, resolve_db_url
from blizzard_mock.mock_data.internal.reflected_store import ReflectedStore, create_seed_engine

_STORE_CHOICES = click.Choice(["hub", "runner"])
_STATUS_CHOICES = click.Choice(STATUSES)
_COMPOSITION_ERRORS = (SchemaDriftError, ChunkCompositionError, GraphCompositionError)


def _resolve_url(store: str, url: str | None, runtime_dir: str | None) -> str:
    if runtime_dir:
        try:
            return resolve_db_url(Path(runtime_dir), store=store)
        except HubRuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
    if not url:
        raise click.UsageError("no store URL — pass --url, --dir, or set DATABASE_URL (sqlite path or postgres DSN)")
    return url


def _seed_service(url: str) -> SeedService:
    return SeedService(ReflectedStore(create_seed_engine(url)))


def _resolve_graph(service: SeedService, name: str | None, clock: Clock, rng: random.Random) -> GraphContext:
    """Resolve ``--graph <name>`` (or its absence) to a :class:`GraphContext`.

    Named and found — reuse the newest matching ``graphs`` row (by ``created_at``).
    Named and absent, or no name given with the store holding no graph at all — mint
    a fresh one (:func:`compose_graph`, under ``name`` or :data:`DEFAULT_GRAPH_NAME`)
    and write it immediately, so ``create chunk`` never errors merely because a
    freshly provisioned store's ``graphs`` table starts empty (the real hub's own
    default graph is minted lazily, on first ingest). No name given with an existing
    graph present reuses the newest one across the whole store."""
    existing = service.query("graphs", {"name": name} if name else None)
    if existing:
        graph_row = max(existing, key=lambda row: str(row["created_at"]))
        node_rows = service.query("graph_nodes", {"graph_id": graph_row["graph_id"]})
        return hydrate_graph_context(graph_row, node_rows)
    minted = compose_graph(name or DEFAULT_GRAPH_NAME, clock, rng)
    service.seed(minted.rows)
    return minted.context


def _parse_work_ref(raw: str) -> tuple[str, str]:
    source, sep, ref = raw.partition("#")
    if not sep:
        raise click.UsageError(f"--work-ref {raw!r} is not SOURCE#REF")
    return source, ref


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


_URL_HELP = "Store URL (sqlite path or postgres DSN). Defaults to $DATABASE_URL."
_DIR_HELP = "A hub/runner runtime dir — reads its blizzard-hub.toml/blizzard-runner.toml `db_url` (sugar for --url)."


@cli.command()
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to reset (labels the target).")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
def reset(store: str, url: str | None, runtime_dir: str | None) -> None:
    """Return a store to a known-clean state — delete every row, FK-safe.

    Reflects the live schema and deletes from every table in reverse dependency order, so
    the next test run starts from the same ground. The tables (the facts-only schema)
    survive; only the rows go.
    """
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    try:
        summary = service.reset()
    except SchemaDriftError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"reset {store} store: cleared {summary.rows_deleted} row(s) across {summary.table_count} table(s)")


@cli.group()
def create() -> None:
    """Create one domain-model instance — a subcommand per concept.

    Not a raw table name: ``runner`` seeds the hub's fleet registry, ``graph`` mints a
    synthetic workflow graph, ``chunk`` composes one chunk at a requested derived
    status. An unknown subcommand is click's own "No such command" — there is no
    stub fallback here any more (compare ``fixture``, still a stub subgroup).
    """


@create.command("runner")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--runner-id", "runner_id", default="runner-seed", help="The runner id.")
@click.option("--workspace-id", "workspace_id", default="workspace-seed", help="The workspace binding.")
@click.option("--paused", is_flag=True, default=False, help="Also land a pause fact.")
def create_runner(
    store: str, url: str | None, runtime_dir: str | None, runner_id: str, workspace_id: str, paused: bool
) -> None:
    """Seed one registered runner into the hub's fleet registry.

    With ``--paused``, also lands a pause fact the runner reads back on its pull.
    """
    if store != "hub":
        raise click.UsageError("'runner' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    now = SystemClock().now()
    rows = [
        FactRow(
            table="runner_registrations",
            values={"runner_id": runner_id, "workspace_id": workspace_id, "registered_at": now, "last_seen_at": now},
        )
    ]
    if paused:
        rows.append(
            FactRow(
                table="runner_pause_facts",
                values={"runner_id": runner_id, "paused": True, "set_at": now, "set_by": "mock-data"},
            )
        )
    try:
        service.seed(rows)
    except SchemaDriftError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created runner {runner_id!r} in the hub store (paused={paused})")


@create.command("graph")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--name", "name", default=DEFAULT_GRAPH_NAME, help="The graph's name.")
@click.option("--seed", "seed", type=int, default=None, help="Seed the id-minting RNG for reproducible ids.")
def create_graph(store: str, url: str | None, runtime_dir: str | None, name: str, seed: int | None) -> None:
    """Mint a synthetic workflow graph — a ``build`` (runner) node into a ``deliver``
    (hub) node into the reserved terminal (``domain/graph_seed.py``).

    A freshly provisioned hub mints no graph of its own until first ingest; this is
    what ``create chunk`` needs to have one to pin a chunk to. Always mints a fresh
    ``graph_id`` (like the real hub's own re-ingest), even when ``--name`` repeats an
    existing graph's name — pass the same ``--name`` to a later ``create chunk
    --graph`` to reuse the row this call just minted instead of minting another.
    Prints the minted graph id, alone, on stdout.
    """
    if store != "hub":
        raise click.UsageError("'graph' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    minted = compose_graph(name, SystemClock(), seeded_rng(seed))
    try:
        service.seed(minted.rows)
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(minted.context.graph_id)


@create.command("chunk")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--status", "status", type=_STATUS_CHOICES, required=True, help="The chunk's derived status.")
@click.option(
    "--graph", "graph_name", default=None, help="Reuse an existing minted graph by this name, or mint one under it."
)
@click.option("--node", "node_name", default=None, help="The graph node the chunk's transition lands on.")
@click.option("--work-ref", "work_refs", multiple=True, metavar="SOURCE#REF", help="A work ref to attach — repeatable.")
@click.option("--runner-id", "runner_id", default="runner-seed", help="The runner id attributed to the chunk's facts.")
@click.option("--epoch", "epoch", type=int, default=1, help="The fencing epoch attributed to the chunk's facts.")
@click.option("--chunk-id", "chunk_id", default=None, help="Override the minted chunk id.")
@click.option("--seed", "seed", type=int, default=None, help="Seed the id-minting RNG for reproducible ids.")
def create_chunk(
    store: str,
    url: str | None,
    runtime_dir: str | None,
    status: str,
    graph_name: str | None,
    node_name: str | None,
    work_refs: tuple[str, ...],
    runner_id: str,
    epoch: int,
    chunk_id: str | None,
    seed: int | None,
) -> None:
    """Compose and write one chunk's fact rows so it derives ``--status``.

    Never a status column (``bzh:facts-not-status``): this writes the exact fact
    rows the hub's own ``derive_chunk_status`` reads, precedence-ordered
    (``domain/chunk_seed.py``). Auto-mints a synthetic graph (``create graph``'s own
    logic) when the store holds none, or when ``--graph`` names one absent; reuses an
    existing one otherwise. Prints the minted chunk id, alone, on stdout — pipeable
    into a sibling verb (a future ``create usage``/``create lease``/etc.).
    """
    if store != "hub":
        raise click.UsageError("'chunk' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    clock = SystemClock()
    rng = seeded_rng(seed)
    try:
        graph = _resolve_graph(service, graph_name, clock, rng)
        refs = [_parse_work_ref(raw) for raw in work_refs]
        seeded = compose_chunk(
            status=status,
            graph=graph,
            clock=clock,
            rng=rng,
            chunk_id=chunk_id,
            node_name=node_name,
            work_refs=refs,
            runner_id=runner_id,
            epoch=epoch,
        )
        service.seed(seeded.rows)
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(seeded.chunk_id)


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
