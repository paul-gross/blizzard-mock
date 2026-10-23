"""The seeding seam — the store Protocol concept composers write against.

Declares ``ISeedStore`` (``bzh:dependency-inversion``): the store operations
a concept composition needs, owned inward. ``SeedService`` is the ``concept
-> list[FactRow]`` orchestrator concept composers hand their rows to.

Also owns the tenant and project rules every write and read applies: which tenant
a tenanted store resolves to (:func:`resolve_tenant`), which project its rows land
in (:func:`resolve_project`), and the column-conditional stamping that carries both,
and a runner store's own runner id, onto a composed row (:func:`stamp_rows`). Composers name neither; the store stamps
them wherever its schema has the column, so the same composition seeds a store from
before tenancy or projects and one after them.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.schema_contract import ReflectedTable

#: The table a tenanted hub store records its tenants in — global, never stamped.
TENANTS_TABLE = "tenants"

#: The column every tenant-owned table carries; a table holds one tenant's rows exactly when it has it.
TENANT_COLUMN = "tenant_id"

#: A runner store's identity table — the id its hub minted, held in the table's own ``runner_id``.
#: Its presence is what marks a store as a runner store; its rows are never stamped.
RUNNER_IDENTITY_TABLE = "runner_identity"

#: The column every runner-scoped table of a runner store carries — the id of the runner whose row it is.
RUNNER_COLUMN = "runner_id"

#: A runner store's outbox counters: one ``(runner_id, lane, last_seq)`` row per runner and lane, the
#: greatest ``seq`` that lane has issued. Once the store carries it, each outbox row's ``seq`` comes from it.
OUTBOX_COUNTERS_TABLE = "outbox_counters"
OUTBOX_SEQ_COLUMN = "seq"

#: Each outbox table and the counter lane its ``seq`` is drawn from.
OUTBOX_LANES: Mapping[str, str] = {"outbound_buffer": "outbound", "transcript_outbound_buffer": "transcript"}

#: A scope's surrogate key, minted by the writer once the store's ``scopes`` table carries it.
SCOPES_TABLE = "scopes"
SCOPE_ID_COLUMN = "scope_id"


#: The hub's ``projects`` table, and the slug of the project every tenant starts with.
PROJECTS_TABLE = "projects"
DEFAULT_PROJECT_SLUG = "default"

#: The column every project-owned table carries; a required one is stamped with the tenant's project.
PROJECT_COLUMN = "project_id"

#: A runner's served-projects declaration: a JSON list of ``{entry, project_id}`` pairs on its
#: registration row, what the runner declared and the project each entry resolved to.
RUNNER_REGISTRATIONS_TABLE = "runner_registrations"
SERVED_PROJECTS_COLUMN = "projects"

#: The hub's built-in work source's per-tenant counter, seeded when a tenant is created.
WORK_ITEM_SEQUENCE_TABLE = "work_item_sequence"

#: The hub tables that carry no tenant key, kept in step by hand with the ``GLOBAL_TABLES``
#: the hub's schema module declares — the list a ``reset`` falls back to when that module
#: can't be imported, which is the usual case: this tool never depends on ``blizzard``.
HUB_GLOBAL_TABLES: frozenset[str] = frozenset(
    {
        TENANTS_TABLE,
        "tenant_deletions",
        "users",
        "identities",
        "sessions",
        "auth_state",
        "auth_facts",
        "superuser_bootstrap",
        "hub_admin_facts",
        "config_import_facts",
        "membership_facts",
        "hub_event_log",
        "trace_cursor",
        "egress_cursor",
    }
)


@dataclass(frozen=True)
class ResetSummary:
    """What a ``reset`` cleared — the ``reset`` verb's reported shape. ``table_count``
    counts the tables it deleted from; ``tables_kept``, those it left whole."""

    rows_deleted: int
    table_count: int
    tables_kept: int = 0


@dataclass(frozen=True)
class ResetBoundary:
    """What a ``reset`` leaves standing. ``kept_tables`` are left whole; each table in
    ``kept_rows`` keeps only the rows matching its equality filter and loses the rest.
    Every other table is emptied. A name the store has no table for is ignored. When
    ``requires_table`` is set, the boundary holds only in a store that has that table;
    a store without it is emptied whole."""

    kept_tables: frozenset[str] = frozenset()
    kept_rows: Mapping[str, Mapping[str, object]] = field(default_factory=dict[str, Mapping[str, object]])
    requires_table: str | None = None

    def for_store(self, store_tables: Collection[str]) -> ResetBoundary:
        """The boundary a store holding ``store_tables`` is reset to."""
        if self.requires_table is not None and self.requires_table not in store_tables:
            return CLEAR_EVERYTHING
        return self


#: A reset that empties every table — the runner store's, and a hub's from before tenancy.
CLEAR_EVERYTHING = ResetBoundary()


def hub_reset_boundary(global_tables: frozenset[str]) -> ResetBoundary:
    """The hub store's reset boundary: ``global_tables`` stay whole, and so does each
    tenant's starting state — its ``tenants`` row, its ``work_item_sequence`` row, and
    its ``default`` project — so the hub is still a usable hub of the same tenants once
    every other row is gone. Nothing re-creates a tenant after a reset (the hub's own
    migration inserts the first one), so deleting these would strand the store. A hub
    from before tenancy has no ``tenants`` table and no boundary: it is emptied whole."""
    return ResetBoundary(
        kept_tables=global_tables | {TENANTS_TABLE, WORK_ITEM_SEQUENCE_TABLE},
        kept_rows={PROJECTS_TABLE: {"slug": DEFAULT_PROJECT_SLUG}},
        requires_table=TENANTS_TABLE,
    )


class SeedIntegrityError(Exception):
    """A composed row doesn't satisfy a constraint the drift guard can't see —
    a dangling foreign key, or a unique constraint the store already carries.
    Named actionably rather than surfacing as a raw traceback.
    """


class TenantResolutionError(Exception):
    """The store's tenant can't be settled: the store holds several and none was
    named, the named one isn't there, or a tenant was named against a store that
    has no tenants. The message lists the tenants to choose from."""


class RunnerResolutionError(Exception):
    """The runner id a runner store's rows are stamped with can't be settled: the named
    runner disagrees with the id in the store's own identity row."""


@dataclass(frozen=True)
class TenantRecord:
    """One tenant a store holds — its id, and the name a person reads beside it."""

    tenant_id: str
    name: str


def _listing(tenants: Sequence[TenantRecord]) -> str:
    return ", ".join(f"{tenant.tenant_id} ({tenant.name})" for tenant in tenants)


def resolve_tenant(tenants: Sequence[TenantRecord], named: str | None) -> str:
    """The tenant a write or read lands in: ``named`` when the store holds it, else the
    store's only tenant. Refuses, listing the store's tenants, when ``named`` is not
    among them or when the store holds several and none was named. A tenant is named by
    its id only — never by its name, which is unique nowhere."""
    if named is not None:
        if any(tenant.tenant_id == named for tenant in tenants):
            return named
        held = _listing(tenants) or "none"
        raise TenantResolutionError(f"--tenant {named!r} names no tenant in this store — it holds: {held}")
    if len(tenants) == 1:
        return tenants[0].tenant_id
    if not tenants:
        raise TenantResolutionError(
            f"the store's {TENANTS_TABLE!r} table holds no tenant — a migrated hub always holds one; "
            "re-migrate the store"
        )
    raise TenantResolutionError(
        f"the store holds {len(tenants)} tenants — pass --tenant <tenant_id> naming one of: {_listing(tenants)}"
    )


class ProjectResolutionError(Exception):
    """The project a seeded row lands in can't be settled: the tenant holds several
    projects and the composer named none, or it holds none at all. The message lists
    the projects to choose from."""


@dataclass(frozen=True)
class ProjectRecord:
    """One project a tenant holds — its id, slug, and the name a person reads beside it."""

    project_id: str
    slug: str
    name: str


def resolve_project(projects: Sequence[ProjectRecord]) -> str:
    """The project a seeded row that names none lands in: the tenant's only project.
    Refuses, listing the tenant's projects, when it holds several — a row is never
    placed by guessing among them — or none."""
    if len(projects) == 1:
        return projects[0].project_id
    if not projects:
        raise ProjectResolutionError(
            f"the tenant holds no project in the store's {PROJECTS_TABLE!r} table — a migrated hub gives every "
            "tenant one; re-migrate the store"
        )
    listing = ", ".join(f"{project.project_id} ({project.slug}: {project.name})" for project in projects)
    raise ProjectResolutionError(
        f"the tenant holds {len(projects)} projects, so a seeded row must name its {PROJECT_COLUMN!r} — "
        f"this tool seeds only a tenant holding one project; it holds: {listing}"
    )


def served_declaration(project_id: str) -> str:
    """A runner registration's served-projects JSON declaring ``project_id`` by its id —
    the shape the hub's own migration gives every runner that declared nothing."""
    return json.dumps([{"entry": project_id, "project_id": project_id}])


def _needs_project_id(row: FactRow, table: ReflectedTable) -> bool:
    column = table.columns.get(PROJECT_COLUMN)
    return (
        row.table != PROJECTS_TABLE and column is not None and column.is_required and PROJECT_COLUMN not in row.values
    )


def _needs_declaration(row: FactRow, table: ReflectedTable) -> bool:
    return (
        row.table == RUNNER_REGISTRATIONS_TABLE
        and SERVED_PROJECTS_COLUMN in table.columns
        and SERVED_PROJECTS_COLUMN not in row.values
    )


def needs_project(rows: Sequence[FactRow], schema: Mapping[str, ReflectedTable]) -> bool:
    """Whether stamping ``rows`` needs the tenant's project: some row belongs to a table
    whose ``project_id`` is required and composes none, or is a runner registration whose
    table carries a served-projects declaration and composes none."""
    for row in rows:
        table = schema.get(row.table)
        if table is not None and (_needs_project_id(row, table) or _needs_declaration(row, table)):
            return True
    return False


def is_tenant_owned(table: ReflectedTable) -> bool:
    """Whether ``table`` holds one tenant's rows — it carries :data:`TENANT_COLUMN`."""
    return TENANT_COLUMN in table.columns


def is_runner_scoped(table_name: str, table: ReflectedTable) -> bool:
    """Whether ``table`` — a runner store's — holds one runner's rows: it carries
    :data:`RUNNER_COLUMN` and is not the identity table that column names into."""
    return table_name != RUNNER_IDENTITY_TABLE and RUNNER_COLUMN in table.columns


def stamp_rows(
    rows: Sequence[FactRow],
    schema: Mapping[str, ReflectedTable],
    tenant_id: str | None,
    mint_scope_id: Callable[[], str],
    project_id: str | None = None,
    runner_id: str | None = None,
) -> list[FactRow]:
    """``rows`` with what the live schema asks of every writer and no composer supplies:
    ``tenant_id`` on each row of a tenant-owned table (when a tenant was resolved); when a
    project was resolved, ``project_id`` on each row of a table that requires it and a
    declaration serving that project on each runner registration whose table carries
    one; and a minted ``scope_id`` on each ``scopes`` row once that table carries the
    column. ``runner_id`` is given only for a runner store: it lands on each row of a
    runner-scoped table (:func:`is_runner_scoped`); a store that can name no runner passes
    none, refused by :func:`require_runner` where the column is required, else left NULL.
    A value the composer already supplied wins — a nullable ``project_id`` left
    out stays NULL, the tenant-level row it means; a table the schema lacks is passed
    through for the drift guard to name."""
    stamped: list[FactRow] = []
    for row in rows:
        table = schema.get(row.table)
        if table is None:
            stamped.append(row)
            continue
        extra: dict[str, object] = {}
        if tenant_id is not None and is_tenant_owned(table) and TENANT_COLUMN not in row.values:
            extra[TENANT_COLUMN] = tenant_id
        if runner_id is not None and is_runner_scoped(row.table, table) and RUNNER_COLUMN not in row.values:
            extra[RUNNER_COLUMN] = runner_id
        if project_id is not None and _needs_project_id(row, table):
            extra[PROJECT_COLUMN] = project_id
        if project_id is not None and _needs_declaration(row, table):
            extra[SERVED_PROJECTS_COLUMN] = served_declaration(project_id)
        if row.table == SCOPES_TABLE and SCOPE_ID_COLUMN in table.columns and SCOPE_ID_COLUMN not in row.values:
            extra[SCOPE_ID_COLUMN] = mint_scope_id()
        stamped.append(FactRow(table=row.table, values={**row.values, **extra}) if extra else row)
    return stamped


def require_runner(rows: Sequence[FactRow], schema: Mapping[str, ReflectedTable]) -> None:
    """Refuse a row of a runner-scoped table that requires :data:`RUNNER_COLUMN` and was
    stamped none — a runner store named by ``--url`` whose runner never registered."""
    for row in rows:
        table = schema.get(row.table)
        if table is None or not is_runner_scoped(row.table, table) or RUNNER_COLUMN in row.values:
            continue
        if table.columns[RUNNER_COLUMN].is_required:
            raise SeedIntegrityError(
                f"{row.table!r} rows need a runner id, and this runner store names none — pass --dir with the "
                "host directory (its [[runner]] id), or --runner-id where the verb takes one"
            )


def assign_outbox_seqs(
    rows: Sequence[FactRow], last_issued: Callable[[str, str], int]
) -> tuple[list[FactRow], dict[tuple[str, str], int]]:
    """``rows`` with each outbox row (:data:`OUTBOX_LANES`) that composes no ``seq`` given the
    next one of its ``(runner_id, lane)``, counting on from ``last_issued(runner_id, lane)``;
    and the last ``seq`` each touched lane now holds, for the counter to advance to. A ``seq``
    a composer supplied is kept and counted, so no later row reuses it. Rows must already
    carry their ``runner_id``; one that does not is passed through."""
    issued: dict[tuple[str, str], int] = {}
    assigned: list[FactRow] = []
    for row in rows:
        lane = OUTBOX_LANES.get(row.table)
        runner_id = row.values.get(RUNNER_COLUMN)
        if lane is None or runner_id is None:
            assigned.append(row)
            continue
        key = (str(runner_id), lane)
        last = issued[key] if key in issued else last_issued(*key)
        supplied = row.values.get(OUTBOX_SEQ_COLUMN)
        if isinstance(supplied, int):
            issued[key] = max(last, supplied)
            assigned.append(row)
            continue
        issued[key] = last + 1
        assigned.append(FactRow(table=row.table, values={**row.values, OUTBOX_SEQ_COLUMN: last + 1}))
    return assigned, issued


class ISeedStore(Protocol):
    """The store operations a concept composition needs — owned by the domain."""

    def write(self, rows: Sequence[FactRow]) -> None:
        """Stamp ``rows`` with the store's tenant, project and runner (:func:`stamp_rows`), validate them
        against the live schema, then insert them
        transactionally, FK-safe (parents before children). Raises
        :class:`SeedIntegrityError` when a row violates a constraint the
        schema drift guard doesn't check (a dangling foreign key, a unique
        clash), :class:`TenantResolutionError` when a row is tenant-owned and the
        store's tenant can't be settled, and :class:`ProjectResolutionError` when a row
        needs the tenant's project (:func:`needs_project`) and it can't be settled."""
        ...

    def reset(self, boundary: ResetBoundary = CLEAR_EVERYTHING) -> ResetSummary:
        """Delete every row ``boundary`` does not keep, FK-safe (children before parents)."""
        ...

    def query(self, table: str, where: Mapping[str, object] | None = None) -> list[Mapping[str, object]]:
        """Read ``table``'s rows, filtered by an equality ``where`` (every row when
        ``where`` is falsy) — the read half a concept composer's "reuse if present"
        lookup needs (e.g. ``create chunk --graph <name>`` finding an existing
        minted graph before deciding whether to mint one). A tenant-owned table reads
        only the store's tenant's rows."""
        ...

    def has_column(self, table: str, column: str) -> bool:
        """Whether the live schema's ``table`` carries ``column`` — the read a composer
        needs before composing rows whose shape follows the schema's version (e.g. the
        seeded graph naming a hub node's ``action`` only where ``graph_nodes`` has one)."""
        ...


class SeedService:
    """Orchestrates ``concept -> list[FactRow]`` composition against a store."""

    def __init__(self, store: ISeedStore) -> None:
        self._store = store

    def seed(self, rows: Sequence[FactRow]) -> None:
        """Hand a composed concept's rows to the store."""
        self._store.write(rows)

    def reset(self, boundary: ResetBoundary = CLEAR_EVERYTHING) -> ResetSummary:
        """Return the store to a known-clean state, keeping what ``boundary`` keeps."""
        return self._store.reset(boundary)

    def query(self, table: str, where: Mapping[str, object] | None = None) -> list[Mapping[str, object]]:
        """Read a table's rows through the store, for a concept composer's
        existence lookup (see :meth:`ISeedStore.query`)."""
        return self._store.query(table, where)

    def has_column(self, table: str, column: str) -> bool:
        """Whether the store's ``table`` carries ``column`` (see :meth:`ISeedStore.has_column`)."""
        return self._store.has_column(table, column)
