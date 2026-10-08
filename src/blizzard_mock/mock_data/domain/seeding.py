"""The seeding seam — the store Protocol concept composers write against.

Declares ``ISeedStore`` (``bzh:dependency-inversion``): the store operations
a concept composition needs, owned inward. ``SeedService`` is the ``concept
-> list[FactRow]`` orchestrator concept composers hand their rows to.

Also owns the tenant rules every write and read applies: which tenant a
tenanted store resolves to (:func:`resolve_tenant`), and the column-conditional
stamping that carries it onto a composed row (:func:`stamp_rows`). Composers
never name a tenant; the store stamps one wherever its schema has the column,
so the same composition seeds a store from before tenancy and one after it.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.schema_contract import ReflectedTable

#: The table a tenanted hub store records its tenants in — global, never stamped.
TENANTS_TABLE = "tenants"

#: The column every tenant-owned table carries; a table holds one tenant's rows exactly when it has it.
TENANT_COLUMN = "tenant_id"

#: A scope's surrogate key, minted by the writer once the store's ``scopes`` table carries it.
SCOPES_TABLE = "scopes"
SCOPE_ID_COLUMN = "scope_id"


#: The hub's ``projects`` table, and the slug of the project every tenant starts with.
PROJECTS_TABLE = "projects"
DEFAULT_PROJECT_SLUG = "default"

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


def is_tenant_owned(table: ReflectedTable) -> bool:
    """Whether ``table`` holds one tenant's rows — it carries :data:`TENANT_COLUMN`."""
    return TENANT_COLUMN in table.columns


def stamp_rows(
    rows: Sequence[FactRow],
    schema: Mapping[str, ReflectedTable],
    tenant_id: str | None,
    mint_scope_id: Callable[[], str],
) -> list[FactRow]:
    """``rows`` with what the live schema asks of every writer and no composer supplies:
    ``tenant_id`` on each row of a tenant-owned table (when a tenant was resolved), and a
    minted ``scope_id`` on each ``scopes`` row once that table carries the column. A value
    the composer already supplied wins; a table the schema lacks is passed through for the
    drift guard to name."""
    stamped: list[FactRow] = []
    for row in rows:
        table = schema.get(row.table)
        if table is None:
            stamped.append(row)
            continue
        extra: dict[str, object] = {}
        if tenant_id is not None and is_tenant_owned(table) and TENANT_COLUMN not in row.values:
            extra[TENANT_COLUMN] = tenant_id
        if row.table == SCOPES_TABLE and SCOPE_ID_COLUMN in table.columns and SCOPE_ID_COLUMN not in row.values:
            extra[SCOPE_ID_COLUMN] = mint_scope_id()
        stamped.append(FactRow(table=row.table, values={**row.values, **extra}) if extra else row)
    return stamped


class ISeedStore(Protocol):
    """The store operations a concept composition needs — owned by the domain."""

    def write(self, rows: Sequence[FactRow]) -> None:
        """Stamp ``rows`` with the store's tenant (:func:`stamp_rows`), validate them
        against the live schema, then insert them
        transactionally, FK-safe (parents before children). Raises
        :class:`SeedIntegrityError` when a row violates a constraint the
        schema drift guard doesn't check (a dangling foreign key, a unique
        clash), and :class:`TenantResolutionError` when a row is tenant-owned and the
        store's tenant can't be settled."""
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
