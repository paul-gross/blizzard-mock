"""Unit coverage for the reflection adapter's drift guard (``blizzard-mock:unit-test``).

Exercises ``internal/reflected_store.ReflectedStore`` directly against a real sqlite
store — no CLI, no ``blizzard`` import — proving the three drift-guard checks
(``domain/schema_contract.check_drift``) each name the offending table/column, and that
a write lands its rows in FK-safe (parents-before-children) order regardless of the
order the caller supplied them in.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    select,
    text,
)

from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.schema_contract import SchemaDriftError
from blizzard_mock.mock_data.domain.seeding import SeedIntegrityError, TenantResolutionError
from blizzard_mock.mock_data.internal.reflected_store import ReflectedStore, create_seed_engine


def _hub_store(tmp_path: Path) -> tuple[str, Table, Table]:
    """A sqlite store mirroring the hub's ``runner_registrations`` + ``runner_pause_facts``."""
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    registrations = Table(
        "runner_registrations",
        meta,
        Column("runner_id", String, primary_key=True),
        Column("workspace_id", String, nullable=False),
        Column("registered_at", DateTime, nullable=False),
        Column("last_seen_at", DateTime, nullable=False),
    )
    pause_facts = Table(
        "runner_pause_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
        Column("paused", Boolean, nullable=False),
        Column("set_at", DateTime, nullable=False),
        Column("set_by", String, nullable=False),
    )
    meta.create_all(engine)
    return url, registrations, pause_facts


_TENANT_A = "ten_01HK153X00AAAAAAAAAAAAAAAA"
_TENANT_B = "ten_01HK153X00BBBBBBBBBBBBBBBB"


def _tenanted_hub_store(tmp_path: Path, *tenant_ids: str) -> tuple[str, MetaData]:
    """A sqlite store shaped like the hub once tenancy lands: a ``tenants`` table holding
    ``tenant_ids``, a global ``users`` table, tenant-owned ``runner_registrations`` +
    ``runner_pause_facts`` carrying ``tenant_id NOT NULL REFERENCES tenants``, and a
    tenant-owned ``scopes`` table keyed by its surrogate ``scope_id``."""
    url = f"sqlite:///{tmp_path / 'tenanted-hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    tenants = Table(
        "tenants",
        meta,
        Column("tenant_id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("created_by", String, nullable=False),
    )
    Table("users", meta, Column("user_id", String, primary_key=True), Column("email", String, nullable=False))
    Table(
        "runner_registrations",
        meta,
        Column("runner_id", String, primary_key=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("workspace_id", String, nullable=False),
        Column("registered_at", DateTime, nullable=False),
        Column("last_seen_at", DateTime, nullable=False),
    )
    Table(
        "runner_pause_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
        Column("paused", Boolean, nullable=False),
        Column("set_at", DateTime, nullable=False),
        Column("set_by", String, nullable=False),
    )
    Table(
        "scopes",
        meta,
        Column("scope_id", String, primary_key=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("slug", String, nullable=False),
    )
    meta.create_all(engine)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        for index, tenant_id in enumerate(tenant_ids):
            conn.execute(
                tenants.insert().values(tenant_id=tenant_id, name=f"tenant-{index}", created_at=now, created_by="t")
            )
    return url, meta


def _registration(runner_id: str, **extra: object) -> FactRow:
    now = datetime.now(UTC)
    return FactRow(
        table="runner_registrations",
        values={"runner_id": runner_id, "workspace_id": "ws", "registered_at": now, "last_seen_at": now, **extra},
    )


def _store(url: str, *, tenant: str | None = None) -> ReflectedStore:
    return ReflectedStore(create_seed_engine(url), tenant=tenant)


# --- drift guard -------------------------------------------------------------


def test_write_rejects_a_missing_table(tmp_path: Path) -> None:
    url, *_ = _hub_store(tmp_path)
    with pytest.raises(SchemaDriftError) as excinfo:
        _store(url).write([FactRow(table="no_such_table", values={"a": 1})])
    message = str(excinfo.value)
    assert "no_such_table" in message
    assert "blizzard-context:/tooling/store-seeding.md" in message


def test_write_rejects_an_unknown_column(tmp_path: Path) -> None:
    url, *_ = _hub_store(tmp_path)
    now = datetime.now(UTC)
    with pytest.raises(SchemaDriftError) as excinfo:
        _store(url).write(
            [
                FactRow(
                    table="runner_registrations",
                    values={
                        "runner_id": "r1",
                        "workspace_id": "ws",
                        "registered_at": now,
                        "last_seen_at": now,
                        "bogus_column": "nope",
                    },
                )
            ]
        )
    message = str(excinfo.value)
    assert "runner_registrations" in message
    assert "bogus_column" in message


def test_write_rejects_a_missing_required_column(tmp_path: Path) -> None:
    url, *_ = _hub_store(tmp_path)
    now = datetime.now(UTC)
    with pytest.raises(SchemaDriftError) as excinfo:
        _store(url).write(
            [
                FactRow(
                    table="runner_registrations", values={"runner_id": "r1", "registered_at": now, "last_seen_at": now}
                )
            ]
        )
    message = str(excinfo.value)
    assert "runner_registrations" in message
    assert "workspace_id" in message


def test_write_rejects_before_inserting_any_row(tmp_path: Path) -> None:
    """A drift error on the second row leaves the first row's insert uncommitted (one transaction)."""
    url, registrations, _pause = _hub_store(tmp_path)
    now = datetime.now(UTC)
    with pytest.raises(SchemaDriftError):
        _store(url).write(
            [
                FactRow(
                    table="runner_registrations",
                    values={"runner_id": "r1", "workspace_id": "ws", "registered_at": now, "last_seen_at": now},
                ),
                FactRow(table="no_such_table", values={"a": 1}),
            ]
        )
    with create_engine(url).begin() as conn:
        assert conn.execute(select(registrations)).all() == []


# --- query (read-side drift) ---------------------------------------------------


def test_query_rejects_a_where_clause_naming_an_unknown_column(tmp_path: Path) -> None:
    """Regression: ``ReflectedStore.query``'s ``where``-clause lookup indexed
    ``reflected.c[column]`` directly, so a caller naming a column the live schema no
    longer has (a drift on the *read* seam) raised a bare ``KeyError`` instead of the
    tool's own promised ``SchemaDriftError`` naming the table and column."""
    url, *_ = _hub_store(tmp_path)
    with pytest.raises(SchemaDriftError) as excinfo:
        _store(url).query("runner_registrations", {"bogus_column": "nope"})
    message = str(excinfo.value)
    assert "runner_registrations" in message
    assert "bogus_column" in message


# --- FK-safe write order -------------------------------------------------------


def test_write_lands_rows_fk_safe_regardless_of_caller_order(tmp_path: Path) -> None:
    url, registrations, pause_facts = _hub_store(tmp_path)
    now = datetime.now(UTC)
    # The pause fact (the child, FK'd to the registration) is listed FIRST — the store
    # must still write the parent row before the child to satisfy the FK.
    _store(url).write(
        [
            FactRow(
                table="runner_pause_facts", values={"runner_id": "r1", "paused": True, "set_at": now, "set_by": "t"}
            ),
            FactRow(
                table="runner_registrations",
                values={"runner_id": "r1", "workspace_id": "ws", "registered_at": now, "last_seen_at": now},
            ),
        ]
    )
    with create_engine(url).begin() as conn:
        assert [r[0] for r in conn.execute(select(registrations.c.runner_id)).all()] == ["r1"]
        assert [r[0] for r in conn.execute(select(pause_facts.c.runner_id)).all()] == ["r1"]


# --- referential integrity (sqlite FK enforcement) -----------------------------


def test_write_rejects_a_dangling_foreign_key(tmp_path: Path) -> None:
    """sqlite FK enforcement is on for the engine this tool writes through: a child
    row naming a parent that was never written raises ``SeedIntegrityError`` — the
    drift guard only validates column *shape*, so without this a `runner_id` no
    `runner_registrations` row ever names would otherwise land silently."""
    url, _registrations, pause_facts = _hub_store(tmp_path)
    now = datetime.now(UTC)
    with pytest.raises(SeedIntegrityError):
        _store(url).write(
            [
                FactRow(
                    table="runner_pause_facts",
                    values={"runner_id": "ghost", "paused": True, "set_at": now, "set_by": "t"},
                )
            ]
        )
    with create_engine(url).begin() as conn:
        assert conn.execute(select(pause_facts)).all() == []


def test_write_rejects_a_row_that_collides_with_one_already_written(tmp_path: Path) -> None:
    """A unique-constraint clash (e.g. re-writing a primary key the store already
    carries) also raises ``SeedIntegrityError``, not a raw traceback — the
    ``scenario board``-rerun-without-``reset`` case."""
    url, registrations, _pause = _hub_store(tmp_path)
    now = datetime.now(UTC)
    row = FactRow(
        table="runner_registrations",
        values={"runner_id": "r1", "workspace_id": "ws", "registered_at": now, "last_seen_at": now},
    )
    _store(url).write([row])
    with pytest.raises(SeedIntegrityError):
        _store(url).write([row])
    with create_engine(url).begin() as conn:
        assert len(conn.execute(select(registrations)).all()) == 1


# --- tenant stamping -------------------------------------------------------------


def test_write_stamps_the_only_tenant_on_every_tenant_owned_row(tmp_path: Path) -> None:
    """A tenanted store's required ``tenant_id`` is never composed — the store stamps its
    only tenant onto each row of a table that has the column, and onto no other."""
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A)
    now = datetime.now(UTC)
    _store(url).write(
        [
            _registration("r1"),
            FactRow(
                table="runner_pause_facts", values={"runner_id": "r1", "paused": True, "set_at": now, "set_by": "t"}
            ),
            FactRow(table="users", values={"user_id": "usr_1", "email": "a@example.com"}),
        ]
    )
    with create_engine(url).begin() as conn:
        assert conn.execute(select(meta.tables["runner_registrations"].c.tenant_id)).scalars().all() == [_TENANT_A]
        assert conn.execute(select(meta.tables["runner_pause_facts"].c.tenant_id)).scalars().all() == [_TENANT_A]
        assert conn.execute(select(meta.tables["users"].c.user_id)).scalars().all() == ["usr_1"]


def test_write_keeps_a_tenant_the_row_already_carries(tmp_path: Path) -> None:
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A, _TENANT_B)
    _store(url, tenant=_TENANT_A).write([_registration("r1", tenant_id=_TENANT_B)])
    with create_engine(url).begin() as conn:
        assert conn.execute(select(meta.tables["runner_registrations"].c.tenant_id)).scalars().all() == [_TENANT_B]


def test_write_refuses_a_store_of_several_tenants_when_none_is_named(tmp_path: Path) -> None:
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A, _TENANT_B)
    with pytest.raises(TenantResolutionError) as excinfo:
        _store(url).write([_registration("r1")])
    message = str(excinfo.value)
    assert "--tenant" in message
    assert _TENANT_A in message and _TENANT_B in message
    with create_engine(url).begin() as conn:
        assert conn.execute(select(meta.tables["runner_registrations"])).all() == []


def test_write_stamps_the_named_tenant(tmp_path: Path) -> None:
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A, _TENANT_B)
    _store(url, tenant=_TENANT_B).write([_registration("r1")])
    with create_engine(url).begin() as conn:
        assert conn.execute(select(meta.tables["runner_registrations"].c.tenant_id)).scalars().all() == [_TENANT_B]


def test_write_refuses_a_named_tenant_the_store_does_not_hold(tmp_path: Path) -> None:
    url, _meta = _tenanted_hub_store(tmp_path, _TENANT_A)
    with pytest.raises(TenantResolutionError, match="ten_GHOST"):
        _store(url, tenant="ten_GHOST").write([_registration("r1")])


def test_write_refuses_a_tenanted_store_that_holds_no_tenant(tmp_path: Path) -> None:
    url, _meta = _tenanted_hub_store(tmp_path)
    with pytest.raises(TenantResolutionError, match="holds no tenant"):
        _store(url).write([_registration("r1")])


def test_write_refuses_a_named_tenant_against_a_store_without_tenants(tmp_path: Path) -> None:
    """A store from before tenancy (or a runner store) has nothing to name — naming a
    tenant there is refused rather than silently ignored."""
    url, *_ = _hub_store(tmp_path)
    with pytest.raises(TenantResolutionError, match="no 'tenants' table"):
        _store(url, tenant=_TENANT_A).write([_registration("r1")])


def test_write_against_a_store_without_tenants_stamps_nothing(tmp_path: Path) -> None:
    url, registrations, _pause = _hub_store(tmp_path)
    _store(url).write([_registration("r1")])
    with create_engine(url).begin() as conn:
        assert conn.execute(select(registrations.c.runner_id)).scalars().all() == ["r1"]


def test_write_mints_a_scope_id_once_scopes_carry_one(tmp_path: Path) -> None:
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A)
    _store(url).write([FactRow(table="scopes", values={"slug": "core"})])
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(meta.tables["scopes"])).mappings().all()
    assert len(rows) == 1
    assert str(rows[0]["scope_id"]).startswith("scp_")
    assert len(str(rows[0]["scope_id"])) == len("scp_") + 26
    assert rows[0]["tenant_id"] == _TENANT_A


def test_write_leaves_scopes_alone_before_they_carry_a_scope_id(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    scopes = Table("scopes", meta, Column("slug", String, primary_key=True))
    meta.create_all(engine)
    _store(url).write([FactRow(table="scopes", values={"slug": "core"})])
    with engine.begin() as conn:
        assert conn.execute(select(scopes)).mappings().all() == [{"slug": "core"}]


def test_query_reads_only_the_resolved_tenants_rows(tmp_path: Path) -> None:
    url, _meta = _tenanted_hub_store(tmp_path, _TENANT_A, _TENANT_B)
    _store(url, tenant=_TENANT_A).write([_registration("r-a")])
    _store(url, tenant=_TENANT_B).write([_registration("r-b")])
    rows = _store(url, tenant=_TENANT_B).query("runner_registrations")
    assert [row["runner_id"] for row in rows] == ["r-b"]
    with pytest.raises(TenantResolutionError):
        _store(url).query("runner_registrations")
    assert _store(url).query("users") == []


# --- reset (unmigrated store) --------------------------------------------------


def test_reset_rejects_an_unmigrated_store(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    create_engine(url).connect().close()  # create the file with zero tables
    with pytest.raises(SchemaDriftError, match="no tables"):
        _store(url).reset()


def test_reset_leaves_the_migration_tracking_table_alone(tmp_path: Path) -> None:
    """A real Alembic-migrated store carries an ``alembic_version`` row alongside every
    domain table; reflection has no notion of "ours vs. the daemon's" so it comes back
    with everything else. ``reset`` must skip it: deleting that one row un-stamps the
    store's migration state without touching a single domain table, so the *next*
    ``blizzard hub init``/``migrate`` against the same file sees no current revision,
    replays every migration from scratch, and dies on the first ``CREATE TABLE`` that
    already exists — reproduced empirically against a live hub runtime."""
    url, _registrations, _pause = _hub_store(tmp_path)
    engine = create_engine(url)
    meta = MetaData()
    Table("alembic_version", meta, Column("version_num", String, primary_key=True))
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('some_revision')"))

    summary = _store(url).reset()

    assert summary.table_count == 2  # runner_registrations + runner_pause_facts — alembic_version excluded
    with engine.begin() as conn:
        rows = conn.execute(text("SELECT version_num FROM alembic_version")).all()
    assert [row[0] for row in rows] == ["some_revision"]
