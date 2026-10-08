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

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
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


@dataclass(frozen=True)
class ResetSummary:
    """What a ``reset`` cleared — the ``reset`` verb's reported shape."""

    rows_deleted: int
    table_count: int


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

    def reset(self) -> ResetSummary:
        """Delete every row from every table, FK-safe (children before parents)."""
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

    def reset(self) -> ResetSummary:
        """Return the store to a known-clean state."""
        return self._store.reset()

    def query(self, table: str, where: Mapping[str, object] | None = None) -> list[Mapping[str, object]]:
        """Read a table's rows through the store, for a concept composer's
        existence lookup (see :meth:`ISeedStore.query`)."""
        return self._store.query(table, where)
