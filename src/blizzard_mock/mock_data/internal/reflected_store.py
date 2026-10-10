"""The SQLAlchemy reflection adapter — the ``ISeedStore`` implementation.

Reflects the target store's live schema once per invocation (never imports
``blizzard``), and writes every row in a single FK-safe transaction — parents
before children. Rows are stamped with the store's tenant and its project first,
then the drift guard runs; both rule sets live in the domain.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from sqlalchemy import (
    Column,
    Engine,
    Integer,
    MetaData,
    Table,
    and_,
    create_engine,
    delete,
    event,
    insert,
    not_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Delete

from blizzard_mock.clock import SystemClock
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.ids import SCOPE_PREFIX, mint
from blizzard_mock.mock_data.domain.schema_contract import (
    GUIDE,
    ReflectedColumn,
    ReflectedTable,
    SchemaDriftError,
    check_drift,
)
from blizzard_mock.mock_data.domain.seeding import (
    CLEAR_EVERYTHING,
    PROJECTS_TABLE,
    TENANT_COLUMN,
    TENANTS_TABLE,
    ISeedStore,
    ProjectRecord,
    ProjectResolutionError,
    ResetBoundary,
    ResetSummary,
    SeedIntegrityError,
    TenantRecord,
    TenantResolutionError,
    needs_project,
    resolve_project,
    resolve_tenant,
    stamp_rows,
)

# A live daemon may hold the same sqlite file open — set a busy timeout so a
# lock waits rather than immediately raising (pinned by tests/test_pin_mock.py).
_SQLITE_BUSY_TIMEOUT_SECONDS = 30.0

#: Alembic's own bookkeeping table. ``reset`` must never touch it — deleting
#: it breaks the next daemon migrate (pinned by tests/test_mock_data_reflected_store.py).
_MIGRATION_TRACKING_TABLE = "alembic_version"


def _enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """Turn on FK enforcement for this sqlite connection — off by default per connection,
    unlike postgres. Without it an orphan ``--chunk``/``--runner-id`` lands silently
    instead of raising the ``IntegrityError`` :func:`ReflectedStore.write` translates into
    a :class:`SeedIntegrityError` (pinned by tests/test_mock_data_reflected_store.py::
    test_write_rejects_a_dangling_foreign_key)."""
    del connection_record
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def create_seed_engine(url: str) -> Engine:
    """Build the engine this tool writes through, sqlite-safe against a live daemon."""
    connect_args: dict[str, object] = {}
    is_sqlite = url.startswith("sqlite")
    if is_sqlite:
        connect_args["timeout"] = _SQLITE_BUSY_TIMEOUT_SECONDS
    engine = create_engine(url, connect_args=connect_args)
    if is_sqlite:
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)
    return engine


def _is_autoincrement_pk(column: Column, table: Table) -> bool:
    if not column.primary_key:
        return False
    pk_columns = list(table.primary_key.columns)
    if len(pk_columns) != 1 or pk_columns[0].name != column.name:
        return False  # a composite primary key never autoincrements
    if column.autoincrement is False:
        return False
    return isinstance(column.type, Integer)


def _snapshot(meta: MetaData) -> dict[str, ReflectedTable]:
    """A schema-agnostic snapshot of ``meta`` — what the domain drift guard compares against."""
    snapshot: dict[str, ReflectedTable] = {}
    for table in meta.tables.values():
        columns = {
            column.name: ReflectedColumn(
                name=column.name,
                nullable=bool(column.nullable),
                has_default=column.default is not None or column.server_default is not None,
                is_autoincrement_pk=_is_autoincrement_pk(column, table),
            )
            for column in table.columns
        }
        snapshot[table.name] = ReflectedTable(name=table.name, columns=columns)
    return snapshot


def _delete_outside(table: Table, keep: Mapping[str, object] | None) -> Delete:
    """A delete of ``table``'s every row but those matching ``keep``'s equality filter."""
    if not keep:
        return delete(table)
    absent = sorted(set(keep) - set(table.c.keys()))
    if absent:
        raise SchemaDriftError(
            f"schema drift: table {table.name!r} has no column(s) {absent} to tell the rows a reset keeps — see {GUIDE}"
        )
    return delete(table).where(not_(and_(*(table.c[column] == value for column, value in keep.items()))))


class ReflectedStore:
    """The SQLAlchemy reflection adapter implementing ``ISeedStore``.

    ``tenant`` names the tenant (by id) a tenanted store's rows land in and its reads
    are filtered to; unnamed, the store's only tenant. It is resolved once, on the first
    write or read that touches a tenant-owned table, and only against a store that has
    a ``tenants`` table — so a store from before tenancy writes exactly as it always has.
    """

    def __init__(self, engine: Engine, *, tenant: str | None = None) -> None:
        self._engine = engine
        self._named_tenant = tenant
        self._resolved_tenant: str | None = None
        self._scope_rng = random.Random()

    def _reflect(self) -> MetaData:
        meta = MetaData()
        meta.reflect(bind=self._engine)
        return meta

    def _tenant_for(self, meta: MetaData, tables: Iterable[str]) -> str | None:
        """The tenant to stamp or filter by when touching ``tables``; ``None`` when none
        of them is tenant-owned and no tenant was named."""
        touches_tenant_owned = any(name in meta.tables and TENANT_COLUMN in meta.tables[name].c for name in tables)
        if not touches_tenant_owned and self._named_tenant is None:
            return None
        if self._resolved_tenant is None:
            self._resolved_tenant = self._resolve_tenant(meta)
        return self._resolved_tenant

    def _resolve_tenant(self, meta: MetaData) -> str | None:
        tenants = meta.tables.get(TENANTS_TABLE)
        if tenants is None:
            if self._named_tenant is not None:
                raise TenantResolutionError(
                    f"--tenant {self._named_tenant!r} names a tenant, but this store has no "
                    f"{TENANTS_TABLE!r} table — a runner store, or a hub store from before tenancy"
                )
            return None
        with self._engine.connect() as conn:
            records = [
                TenantRecord(tenant_id=str(row.tenant_id), name=str(row.name))
                for row in conn.execute(select(tenants.c.tenant_id, tenants.c.name).order_by(tenants.c.tenant_id)).all()
            ]
        return resolve_tenant(records, self._named_tenant)

    def _resolve_project(self, meta: MetaData) -> str:
        """The project a row that names none lands in: the store tenant's only project.
        Read on every write that needs it, so a project seeded earlier counts."""
        projects = meta.tables.get(PROJECTS_TABLE)
        if projects is None:
            raise ProjectResolutionError(
                f"the store has project-owned tables but no {PROJECTS_TABLE!r} table — re-migrate the store"
            )
        tenant_id = self._tenant_for(meta, [PROJECTS_TABLE])
        name = projects.c.get("name", projects.c.slug)
        stmt = select(projects.c.project_id, projects.c.slug, name.label("name")).order_by(projects.c.project_id)
        if tenant_id is not None and TENANT_COLUMN in projects.c:
            stmt = stmt.where(projects.c[TENANT_COLUMN] == tenant_id)
        with self._engine.connect() as conn:
            records = [
                ProjectRecord(project_id=str(row.project_id), slug=str(row.slug), name=str(row.name))
                for row in conn.execute(stmt).all()
            ]
        return resolve_project(records)

    def _mint_scope_id(self) -> str:
        return mint(SCOPE_PREFIX, SystemClock(), self._scope_rng)

    def write(self, rows: Sequence[FactRow]) -> None:
        if not rows:
            return
        meta = self._reflect()
        schema = _snapshot(meta)
        tenant_id = self._tenant_for(meta, {row.table for row in rows})
        project_id = self._resolve_project(meta) if needs_project(rows, schema) else None
        rows = stamp_rows(rows, schema, tenant_id, self._mint_scope_id, project_id)
        check_drift(schema, rows)
        by_table: dict[str, list[FactRow]] = {}
        for row in rows:
            by_table.setdefault(row.table, []).append(row)
        try:
            with self._engine.begin() as conn:
                for table in meta.sorted_tables:  # parents before children (FK-safe)
                    for row in by_table.get(table.name, []):
                        conn.execute(insert(table).values(**row.values))
        except IntegrityError as exc:
            raise SeedIntegrityError(
                "constraint violation writing seeded rows — a referenced row (e.g. a "
                "--chunk/--runner-id this store never seeded) may not exist, or the store "
                f"already carries this data (try `reset` first): {exc.orig}"
            ) from exc

    def reset(self, boundary: ResetBoundary = CLEAR_EVERYTHING) -> ResetSummary:
        meta = self._reflect()
        if not meta.tables:
            raise SchemaDriftError(
                f"the store has no tables — is it migrated? (run the daemon's `migrate`) — see {GUIDE}"
            )
        boundary = boundary.for_store(meta.tables.keys())
        tables = [table for table in meta.sorted_tables if table.name != _MIGRATION_TRACKING_TABLE]
        kept = [table for table in tables if table.name in boundary.kept_tables]
        reset_tables = [table for table in tables if table.name not in boundary.kept_tables]
        deleted = 0
        with self._engine.begin() as conn:
            for table in reversed(reset_tables):  # children before parents (FK-safe)
                deleted += conn.execute(_delete_outside(table, boundary.kept_rows.get(table.name))).rowcount or 0
        return ResetSummary(rows_deleted=deleted, table_count=len(reset_tables), tables_kept=len(kept))

    def query(self, table: str, where: Mapping[str, object] | None = None) -> list[Mapping[str, object]]:
        meta = self._reflect()
        reflected = meta.tables.get(table)
        if reflected is None:
            raise SchemaDriftError(f"schema drift: table {table!r} does not exist in the live store — see {GUIDE}")
        stmt = select(reflected)
        tenant_id = self._tenant_for(meta, [table])
        if tenant_id is not None and TENANT_COLUMN in reflected.c:
            stmt = stmt.where(reflected.c[TENANT_COLUMN] == tenant_id)
        for column, value in (where or {}).items():
            if column not in reflected.c:
                raise SchemaDriftError(f"schema drift: table {table!r} has no column {column!r} — see {GUIDE}")
            stmt = stmt.where(reflected.c[column] == value)
        with self._engine.connect() as conn:
            return [dict(row) for row in conn.execute(stmt).mappings().all()]


# Typecheck-time Protocol/adapter conformance sentinel (bzh:dependency-inversion) — pyright
# rejects the return if ReflectedStore drifts from ISeedStore.
def _conforms_seed_store(x: ReflectedStore) -> ISeedStore:
    return x
