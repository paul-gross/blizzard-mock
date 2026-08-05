"""The mock-data CLI (``blizzard-mock-data``).

A tool *for agents* to set up test cases repeatably against the real
hub/runner stores. Operates on domain models, not raw tables, and reflects
the live store's schema at runtime rather than importing ``blizzard``.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path

import click

from blizzard_mock.clock import Clock, FixedClock, SystemClock
from blizzard_mock.mock_data.domain.chunk_seed import STATUSES, ChunkCompositionError, compose_chunk
from blizzard_mock.mock_data.domain.escalation_seed import (
    CAUSE_RETRIES,
    CAUSES,
    EscalationCompositionError,
    compose_escalation,
)
from blizzard_mock.mock_data.domain.event_seed import SEVERITIES, EventCompositionError, compose_event
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.graph_seed import (
    DEFAULT_GRAPH_NAME,
    GraphCompositionError,
    GraphContext,
    compose_graph,
    hydrate_graph_context,
)
from blizzard_mock.mock_data.domain.ids import seeded_rng
from blizzard_mock.mock_data.domain.lease_seed import compose_lease_row
from blizzard_mock.mock_data.domain.question_seed import QuestionCompositionError, compose_question
from blizzard_mock.mock_data.domain.runner_pause_seed import RunnerPauseCompositionError, compose_runner_pause
from blizzard_mock.mock_data.domain.scenario_seed import (
    DEFAULT_CHUNKS,
    ScenarioCompositionError,
    compose_board_scenario,
)
from blizzard_mock.mock_data.domain.schema_contract import SchemaDriftError, require_column
from blizzard_mock.mock_data.domain.seeding import SeedIntegrityError, SeedService
from blizzard_mock.mock_data.domain.usage_seed import KINDS as USAGE_KINDS
from blizzard_mock.mock_data.domain.usage_seed import UsageCompositionError, compose_usage
from blizzard_mock.mock_data.internal.hub_runtime import HubRuntimeError, resolve_db_url
from blizzard_mock.mock_data.internal.reflected_store import ReflectedStore, create_seed_engine

_STORE_CHOICES = click.Choice(["hub", "runner"])
_STATUS_CHOICES = click.Choice(STATUSES)
_USAGE_KIND_CHOICES = click.Choice(USAGE_KINDS)
_SEVERITY_CHOICES = click.Choice(SEVERITIES)
_CAUSE_CHOICES = click.Choice(CAUSES)
_COMPOSITION_ERRORS = (
    SchemaDriftError,
    SeedIntegrityError,
    ChunkCompositionError,
    GraphCompositionError,
    UsageCompositionError,
    EscalationCompositionError,
    QuestionCompositionError,
    EventCompositionError,
    RunnerPauseCompositionError,
    ScenarioCompositionError,
)

#: Fallback ``event_log.runner_id`` (NOT NULL) when ``create event`` gets
#: neither ``--runner-id`` nor a ``--chunk`` whose lease history names one.
_DEFAULT_EVENT_RUNNER_ID = "mock-data"

#: Fixed clock instant any ``--seed``\ ed verb pins to, so two invocations at
#: the same seed compose byte-identical timestamps and ids.
_SEEDED_CLOCK_ANCHOR = datetime(2024, 1, 1, tzinfo=UTC)


def _seeded_clock(seed: int | None) -> Clock:
    """A clock pinned to :data:`_SEEDED_CLOCK_ANCHOR` under an explicit ``--seed``, the
    real wall clock otherwise. The clock must be pinned alongside the RNG because
    ``ids.ulid`` draws its leading 48 bits from it (pinned by tests/test_mock_data_cli.py::
    test_create_graph_same_seed_mints_the_same_id_across_two_stores)."""
    return FixedClock(_SEEDED_CLOCK_ANCHOR) if seed is not None else SystemClock()


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

    Reuses the newest matching ``graphs`` row when found; mints and writes a
    fresh one when absent, so ``create chunk`` never errors on an empty store.
    """
    existing = service.query("graphs", {"name": name} if name else None)
    if existing:
        graph_row = max(existing, key=lambda row: str(require_column(row, "created_at", table="graphs")))
        node_rows = service.query("graph_nodes", {"graph_id": require_column(graph_row, "graph_id", table="graphs")})
        return hydrate_graph_context(graph_row, node_rows)
    minted = compose_graph(name or DEFAULT_GRAPH_NAME, clock, rng)
    service.seed(minted.rows)
    return minted.context


def _parse_work_ref(raw: str) -> tuple[str, str]:
    source, sep, ref = raw.partition("#")
    if not sep:
        raise click.UsageError(f"--work-ref {raw!r} is not SOURCE#REF")
    return source, ref


def _resolve_usage_defaults(
    service: SeedService, chunk_id: str, *, node_name: str | None, epoch: int | None, runner_id: str | None
) -> tuple[str, int, str]:
    """Resolve ``create usage``'s ``--node``/``--epoch``/``--runner-id`` against
    ``chunk_id``'s already-seeded rows when not given: epoch/runner id from the
    newest ``lease_facts`` row, node from the newest ``transitions`` row.
    ``--node`` has no fallback and is refused when not derivable.
    """
    resolved_epoch = epoch
    resolved_runner = runner_id
    if resolved_epoch is None or resolved_runner is None:
        leases = service.query("lease_facts", {"chunk_id": chunk_id})
        if leases:
            newest = max(leases, key=lambda row: str(require_column(row, "minted_at", table="lease_facts")))
            resolved_epoch = (
                resolved_epoch
                if resolved_epoch is not None
                else int(require_column(newest, "epoch", table="lease_facts"))  # type: ignore[arg-type]
            )
            resolved_runner = (
                resolved_runner
                if resolved_runner is not None
                else str(require_column(newest, "runner_id", table="lease_facts"))
            )
    resolved_epoch = resolved_epoch if resolved_epoch is not None else 1
    resolved_runner = resolved_runner if resolved_runner is not None else "runner-seed"

    resolved_node = node_name
    if resolved_node is None:
        transitions = service.query("transitions", {"chunk_id": chunk_id})
        if transitions:
            newest = max(transitions, key=lambda row: str(require_column(row, "recorded_at", table="transitions")))
            resolved_node = str(require_column(newest, "to_node_id", table="transitions"))
    if resolved_node is None:
        raise click.UsageError(
            f"--node not given and chunk {chunk_id!r} has no transitions to derive one from — pass --node explicitly"
        )
    return resolved_node, resolved_epoch, resolved_runner


def _resolve_event_runner_id(service: SeedService, chunk_id: str | None, runner_id: str | None) -> str:
    """Resolve ``create event``'s ``--runner-id`` (NOT NULL on the real ``event_log``
    table) when omitted: the named chunk's newest ``lease_facts`` runner, or
    :data:`_DEFAULT_EVENT_RUNNER_ID` absent either — never a crash on the NOT NULL
    column."""
    if runner_id is not None:
        return runner_id
    if chunk_id is not None:
        leases = service.query("lease_facts", {"chunk_id": chunk_id})
        if leases:
            newest = max(leases, key=lambda row: str(require_column(row, "minted_at", table="lease_facts")))
            return str(require_column(newest, "runner_id", table="lease_facts"))
    return _DEFAULT_EVENT_RUNNER_ID


def _not_implemented(what: str) -> None:
    raise click.ClickException(
        f"{what} is not implemented — richer domain rows seed through the daemon's own HTTP API "
        "in the service tier (self-validating). See src/blizzard_mock/mock_data/README.md."
    )


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(package_name="blizzard-mock")
def cli() -> None:
    """Seed, reset, and fixture the hub/runner stores for repeatable test cases.

    Operates on domain models, not raw tables, and reflects the live store's
    schema so it never imports ``blizzard``.
    """


_URL_HELP = "Store URL (sqlite path or postgres DSN). Defaults to $DATABASE_URL."
_DIR_HELP = "A hub/runner runtime dir — reads its blizzard-hub.toml/blizzard-runner.toml `db_url` (sugar for --url)."


@cli.command()
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to reset (labels the target).")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
def reset(store: str, url: str | None, runtime_dir: str | None) -> None:
    """Return a store to a known-clean state — delete every row, FK-safe.

    Reflects the live schema and deletes every table in reverse dependency
    order; the tables (facts-only schema) survive, only the rows go.
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

    Not a raw table name: each subcommand lands one board-concept fact set
    onto an already-seeded chunk or runner.
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

    With ``--paused``, also lands a pause fact.
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
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created runner {runner_id!r} in the hub store (paused={paused})")


@create.command("graph")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--name", "name", default=DEFAULT_GRAPH_NAME, help="The graph's name.")
@click.option(
    "--seed", "seed", type=int, default=None, help="Seed id-minting and pin the clock for byte-identical runs."
)
def create_graph(store: str, url: str | None, runtime_dir: str | None, name: str, seed: int | None) -> None:
    """Mint a synthetic workflow graph — a ``build`` node into a ``deliver`` node.

    Always mints a fresh ``graph_id``, even when ``--name`` repeats an
    existing name. Prints the minted graph id, alone, on stdout.
    """
    if store != "hub":
        raise click.UsageError("'graph' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    minted = compose_graph(name, _seeded_clock(seed), seeded_rng(seed))
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
@click.option(
    "--node",
    "node_name",
    default=None,
    help="The graph node the chunk's transition lands on (--status done: the node it lands from).",
)
@click.option("--work-ref", "work_refs", multiple=True, metavar="SOURCE#REF", help="A work ref to attach — repeatable.")
@click.option("--runner-id", "runner_id", default="runner-seed", help="The runner id attributed to the chunk's facts.")
@click.option("--epoch", "epoch", type=int, default=1, help="The fencing epoch attributed to the chunk's facts.")
@click.option("--chunk-id", "chunk_id", default=None, help="Override the minted chunk id.")
@click.option(
    "--seed", "seed", type=int, default=None, help="Seed id-minting and pin the clock for byte-identical runs."
)
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

    Never a status column (``bzh:facts-not-status``). Auto-mints a synthetic
    graph when the store holds none; prints the minted chunk id on stdout.
    """
    if store != "hub":
        raise click.UsageError("'chunk' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    clock = _seeded_clock(seed)
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


@create.command("usage")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--chunk", "chunk_id", required=True, help="The chunk this usage fact is attributed to.")
@click.option("--kind", "kind", type=_USAGE_KIND_CHOICES, required=True, help="The harness invocation kind.")
@click.option("--model", "model", required=True, help="The model name.")
@click.option("--input-tokens", "input_tokens", type=int, required=True, help="Input tokens.")
@click.option("--output-tokens", "output_tokens", type=int, default=0, help="Output tokens.")
@click.option("--cache-read-tokens", "cache_read_tokens", type=int, default=0, help="Cache-read tokens.")
@click.option("--cache-create-tokens", "cache_create_tokens", type=int, default=0, help="Cache-create tokens.")
@click.option("--cost-usd", "cost_usd", type=float, default=None, help="The invocation's cost, in USD.")
@click.option(
    "--no-cost",
    "no_cost",
    is_flag=True,
    default=False,
    help="No cost envelope for this invocation — lands a genuine SQL NULL cost_usd, never 0.0.",
)
@click.option(
    "--node", "node_name", default=None, help="The node id attributed (default: the chunk's newest transition target)."
)
@click.option(
    "--epoch", "epoch", type=int, default=None, help="The fencing epoch attributed (default: the chunk's newest lease)."
)
@click.option(
    "--runner-id", "runner_id", default=None, help="The reporting runner id (default: the chunk's newest lease)."
)
def create_usage(
    store: str,
    url: str | None,
    runtime_dir: str | None,
    chunk_id: str,
    kind: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_create_tokens: int,
    cost_usd: float | None,
    no_cost: bool,
    node_name: str | None,
    epoch: int | None,
    runner_id: str | None,
) -> None:
    """Land one ``usage_facts`` row against an already-seeded chunk.

    Exactly one of ``--cost-usd``/``--no-cost`` is required — ``--no-cost``
    lands a genuine SQL NULL, never a fabricated ``0.0``.
    """
    if store != "hub":
        raise click.UsageError("'usage' lives in the hub store (--store hub)")
    if cost_usd is not None and no_cost:
        raise click.UsageError("--cost-usd and --no-cost are mutually exclusive")
    if cost_usd is None and not no_cost:
        raise click.UsageError("pass exactly one of --cost-usd or --no-cost")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    try:
        resolved_node, resolved_epoch, resolved_runner = _resolve_usage_defaults(
            service, chunk_id, node_name=node_name, epoch=epoch, runner_id=runner_id
        )
        row = compose_usage(
            chunk_id=chunk_id,
            node_id=resolved_node,
            epoch=resolved_epoch,
            runner_id=resolved_runner,
            kind=kind,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_create_tokens=cache_create_tokens,
            cost_usd=None if no_cost else cost_usd,
            recorded_at=SystemClock().now(),
        )
        service.seed([row])
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created usage fact for chunk {chunk_id!r} (kind={kind}, cost_usd={row.values['cost_usd']!r})")


@create.command("lease")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--chunk", "chunk_id", required=True, help="The chunk to lease.")
@click.option("--runner-id", "runner_id", required=True, help="The runner minting the lease.")
@click.option("--epoch", "epoch", type=int, default=1, help="The fencing epoch.")
def create_lease(
    store: str, url: str | None, runtime_dir: str | None, chunk_id: str, runner_id: str, epoch: int
) -> None:
    """Land one ``lease_facts`` row against an already-seeded chunk — the same row
    shape ``create chunk --status running/delivering`` composes internally
    (``domain/lease_seed.py``)."""
    if store != "hub":
        raise click.UsageError("'lease' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    row = compose_lease_row(chunk_id=chunk_id, epoch=epoch, runner_id=runner_id, minted_at=SystemClock().now())
    try:
        service.seed([row])
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created lease for chunk {chunk_id!r} (epoch={epoch}, runner_id={runner_id!r})")


@create.command("escalation")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--chunk", "chunk_id", required=True, help="The chunk to escalate.")
@click.option("--epoch", "epoch", type=int, default=1, help="The fencing epoch.")
@click.option(
    "--takeover-command",
    "takeover_command",
    default=None,
    help="Override the composed takeover command verbatim (overrides --cause's default either way).",
)
@click.option(
    "--wrapped-takeover-command",
    "wrapped_takeover_command",
    default=None,
    help="Override the composed wrapped (`blizzard runner takeover`) command verbatim. Left unset, a "
    "placeholder is synthesized regardless of --cause.",
)
@click.option(
    "--cause",
    "cause",
    type=_CAUSE_CHOICES,
    default=CAUSE_RETRIES,
    help="What parked the chunk — selects the default takeover-command wording (domain/escalation_seed.py).",
)
def create_escalation(
    store: str,
    url: str | None,
    runtime_dir: str | None,
    chunk_id: str,
    epoch: int,
    takeover_command: str | None,
    wrapped_takeover_command: str | None,
    cause: str,
) -> None:
    """Land one ``escalations`` row against an already-seeded chunk — ``needs_human``
    derives from an open (no later lease/requeue) escalation. Does **not** also write
    an ``event_log`` row (``domain/event_seed.py``; see ``create event``'s docstring)."""
    if store != "hub":
        raise click.UsageError("'escalation' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    try:
        row = compose_escalation(
            chunk_id=chunk_id,
            epoch=epoch,
            recorded_at=SystemClock().now(),
            cause=cause,
            takeover_command=takeover_command,
            wrapped_takeover_command=wrapped_takeover_command,
        )
        service.seed([row])
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created a {cause!r}-cause escalation for chunk {chunk_id!r}")


@create.command("question")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--chunk", "chunk_id", required=True, help="The chunk this question parks.")
@click.option("--text", "question_text", required=True, help="The question text.")
@click.option("--option", "options", multiple=True, help="An offered choice — repeatable.")
@click.option("--answer", "answer", default=None, help="Also land an answer (requires --answered-by).")
@click.option("--answered-by", "answered_by", default=None, help="Who answered (requires --answer).")
@click.option(
    "--delivered", "delivered", is_flag=True, default=False, help="Also land an answer delivery (requires --answer)."
)
@click.option(
    "--resumed",
    "resumed",
    is_flag=True,
    default=False,
    help="Confirm the full resumed trail landed (requires --delivered; lands no additional fact row).",
)
@click.option("--node", "node_name", default=None, help="The node id the worker parked at.")
@click.option("--runner-id", "runner_id", default="runner-seed", help="The runner holding the parked session.")
@click.option("--epoch", "epoch", type=int, default=1, help="The parked lease's fencing epoch.")
@click.option(
    "--seed", "seed", type=int, default=None, help="Seed id-minting and pin the clock for byte-identical runs."
)
def create_question(
    store: str,
    url: str | None,
    runtime_dir: str | None,
    chunk_id: str,
    question_text: str,
    options: tuple[str, ...],
    answer: str | None,
    answered_by: str | None,
    delivered: bool,
    resumed: bool,
    node_name: str | None,
    runner_id: str,
    epoch: int,
    seed: int | None,
) -> None:
    """Land one open-or-answered question trail against an already-seeded chunk.

    ``--answer``/``--answered-by`` also land a ``question_answers`` row;
    ``--delivered`` also lands an ``answer_deliveries`` row.
    """
    if store != "hub":
        raise click.UsageError("'question' lives in the hub store (--store hub)")
    if resumed and not delivered:
        raise click.UsageError("--resumed requires --delivered — there is no fact beyond delivery for a resume")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    clock = _seeded_clock(seed)
    rng = seeded_rng(seed)
    try:
        seeded = compose_question(
            chunk_id=chunk_id,
            clock=clock,
            rng=rng,
            question=question_text,
            options=list(options),
            node_id=node_name,
            runner_id=runner_id,
            epoch=epoch,
            answer=answer,
            answered_by=answered_by,
            delivered=delivered,
        )
        service.seed(seeded.rows)
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(seeded.question_id)


@create.command("event")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--kind", "kind", required=True, help="The event's noun.verb name.")
@click.option("--severity", "severity", type=_SEVERITY_CHOICES, required=True, help="The event's severity.")
@click.option("--message", "message", required=True, help="The event's human-readable message.")
@click.option("--chunk", "chunk_id", default=None, help="The chunk this event concerns, if any.")
@click.option(
    "--runner-id",
    "runner_id",
    default=None,
    help="The reporting runner (NOT NULL on the real table; default: --chunk's newest lease, else 'mock-data').",
)
@click.option("--node", "node_name", default=None, help="The node name this event concerns, if any.")
@click.option("--detail", "detail", default=None, help="Opaque JSON text, round-tripped only — must parse as JSON.")
def create_event(
    store: str,
    url: str | None,
    runtime_dir: str | None,
    kind: str,
    severity: str,
    message: str,
    chunk_id: str | None,
    runner_id: str | None,
    node_name: str | None,
    detail: str | None,
) -> None:
    """Land one ``event_log`` row — the operational event feed. Independent of ``create
    escalation`` (``domain/event_seed.py``)."""
    if store != "hub":
        raise click.UsageError("'event' lives in the hub store (--store hub)")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    try:
        resolved_runner_id = _resolve_event_runner_id(service, chunk_id, runner_id)
        row = compose_event(
            kind=kind,
            severity=severity,
            message=message,
            runner_id=resolved_runner_id,
            chunk_id=chunk_id,
            node_name=node_name,
            detail=detail,
            recorded_at=SystemClock().now(),
        )
        service.seed([row])
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created event {kind!r} (severity={severity})")


@create.command("runner-pause")
@click.option("--store", "store", type=_STORE_CHOICES, required=True, help="Which store to create into.")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option("--runner-id", "runner_id", required=True, help="The runner to pause.")
@click.option(
    "--local", "local", is_flag=True, default=False, help="The runner's own local brake (runner_local_pause_facts)."
)
@click.option(
    "--fleet", "fleet", is_flag=True, default=False, help="The fleet's brake (runner_pause_facts) — no reason column."
)
@click.option(
    "--reason", "reason", default=None, help="Only valid with --local — runner_pause_facts has no reason column."
)
def create_runner_pause(
    store: str, url: str | None, runtime_dir: str | None, runner_id: str, local: bool, fleet: bool, reason: str | None
) -> None:
    """Land one pause fact, engaged — exactly one of ``--local``/``--fleet`` is
    required. ``--fleet --reason`` fails loud (``runner_pause_facts`` has no
    ``reason`` column) rather than silently dropping the reason."""
    if store != "hub":
        raise click.UsageError("'runner-pause' lives in the hub store (--store hub)")
    if local == fleet:
        raise click.UsageError("pass exactly one of --local or --fleet")
    service = _seed_service(_resolve_url(store, url, runtime_dir))
    try:
        row = compose_runner_pause(runner_id=runner_id, local=local, reason=reason, set_at=SystemClock().now())
        service.seed([row])
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"created {'local' if local else 'fleet'} pause fact for runner {runner_id!r}")


@cli.group()
def scenario() -> None:
    """Compose a whole, consistent multi-concept world in one invocation.

    Where ``create``'s verbs each land one concept, ``scenario``'s verbs are
    pure composition on top of them — one command, one realistic board.
    """


@scenario.command("board")
@click.option("--url", "url", envvar="DATABASE_URL", default=None, help=_URL_HELP)
@click.option("--dir", "runtime_dir", default=None, help=_DIR_HELP)
@click.option(
    "--chunks", "chunks", type=int, default=DEFAULT_CHUNKS, show_default=True, help="How many chunks to seed."
)
@click.option(
    "--stress",
    "stress",
    is_flag=True,
    default=False,
    help="Also seed the four narrow-viewport/overflow extremes (long identity, long node name, multi-question chunk).",
)
@click.option(
    "--seed", "seed", type=int, default=None, help="Seed id-minting and pin the clock for byte-identical runs."
)
def scenario_board(url: str | None, runtime_dir: str | None, chunks: int, stress: bool, seed: int | None) -> None:
    """Seed one whole, ready-to-view board: a synthetic graph, ``--chunks``
    chunks spread across all nine derived statuses, a cost spread, a
    ceiling-paused runner, a runner per chunk, and a mixed-severity event log.
    Always the hub store. Prints the store it wrote to and a summary census.
    """
    resolved_url = _resolve_url("hub", url, runtime_dir)
    service = _seed_service(resolved_url)
    clock = _seeded_clock(seed)
    rng = seeded_rng(seed)
    try:
        scenario_seed = compose_board_scenario(chunks=chunks, clock=clock, rng=rng, stress=stress)
        service.seed(scenario_seed.rows)
    except _COMPOSITION_ERRORS as exc:
        raise click.ClickException(str(exc)) from exc

    census = scenario_seed.census
    assert census is not None  # compose_board_scenario always returns one
    click.echo(f"scenario board seeded into the hub store: {resolved_url}")
    click.echo(f"graph: {scenario_seed.graph_id}")
    click.echo(f"chunks: {census.chunk_count}")
    for entry in census.chunk_entries:
        click.echo(f"  chunk {entry.chunk_id} status={entry.status}")
    click.echo(f"status census: {dict(sorted(census.status_counts.items()))}")
    click.echo(f"runners: {census.runner_count} (ceiling-paused: {census.ceiling_paused_runner_id!r})")
    click.echo(f"usage facts: {census.usage_fact_count} (cost-partial: {census.cost_partial_count})")
    click.echo(f"stress extras: {'included' if stress else 'not included'}")


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
