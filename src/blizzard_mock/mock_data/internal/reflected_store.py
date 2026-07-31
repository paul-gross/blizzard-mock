"""The SQLAlchemy reflection adapter — the ``ISeedStore`` implementation.

Reflects the target store's live schema once per invocation (never imports
``blizzard``, so it works against whatever the daemon's Alembic tree migrated),
and writes every row a command composes in a single FK-safe transaction —
parents before children (``meta.sorted_tables``), the inverse of ``reset``'s
children-before-parents delete order. The drift guard
(``domain/schema_contract.py``) runs first, over a schema-agnostic snapshot
this module builds from the reflected ``MetaData`` — the only SQLAlchemy-aware
piece of the drift check; the rules themselves live in the domain.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import Column, Engine, Integer, MetaData, Table, create_engine, delete, insert

from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.schema_contract import (
    GUIDE,
    ReflectedColumn,
    ReflectedTable,
    SchemaDriftError,
    check_drift,
)
from blizzard_mock.mock_data.domain.seeding import ISeedStore, ResetSummary

# A live hub/runner daemon may hold the same sqlite file open — set a busy
# timeout so a lock contends and *waits* rather than this tool immediately
# raising "database is locked" (sqlite3's ``timeout`` connect arg is exactly
# that DBAPI-level busy wait, in seconds). A no-op for postgres: only applied
# when the URL names the sqlite dialect.
_SQLITE_BUSY_TIMEOUT_SECONDS = 30.0


def create_seed_engine(url: str) -> Engine:
    """Build the engine this tool writes through, sqlite-safe against a live daemon."""
    connect_args: dict[str, object] = {}
    if url.startswith("sqlite"):
        connect_args["timeout"] = _SQLITE_BUSY_TIMEOUT_SECONDS
    return create_engine(url, connect_args=connect_args)


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


class ReflectedStore:
    """The SQLAlchemy reflection adapter implementing ``ISeedStore``."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def _reflect(self) -> MetaData:
        meta = MetaData()
        meta.reflect(bind=self._engine)
        return meta

    def write(self, rows: Sequence[FactRow]) -> None:
        if not rows:
            return
        meta = self._reflect()
        check_drift(_snapshot(meta), rows)
        by_table: dict[str, list[FactRow]] = {}
        for row in rows:
            by_table.setdefault(row.table, []).append(row)
        with self._engine.begin() as conn:
            for table in meta.sorted_tables:  # parents before children (FK-safe)
                for row in by_table.get(table.name, []):
                    conn.execute(insert(table).values(**row.values))

    def reset(self) -> ResetSummary:
        meta = self._reflect()
        if not meta.tables:
            raise SchemaDriftError(
                f"the store has no tables — is it migrated? (run the daemon's `migrate`) — see {GUIDE}"
            )
        deleted = 0
        with self._engine.begin() as conn:
            for table in reversed(meta.sorted_tables):  # children before parents (FK-safe)
                deleted += conn.execute(delete(table)).rowcount or 0
        return ResetSummary(rows_deleted=deleted, table_count=len(meta.tables))


# Typecheck-time Protocol/adapter conformance sentinel (bzh:dependency-inversion) — pyright
# rejects the return if ReflectedStore drifts from ISeedStore.
def _conforms_seed_store(x: ReflectedStore) -> ISeedStore:
    return x
