"""Unit coverage for the reflection adapter's drift guard (``blizzard-mock:unit-test``).

Exercises ``internal/reflected_store.ReflectedStore`` directly against a real sqlite
store — no CLI, no ``blizzard`` import — proving the three drift-guard checks
(``domain/schema_contract.check_drift``) each name the offending table/column, and that
a write lands its rows in FK-safe (parents-before-children) order regardless of the
order the caller supplied them in.
"""

from __future__ import annotations

import json
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
from blizzard_mock.mock_data.domain.seeding import (
    HUB_GLOBAL_TABLES,
    ProjectResolutionError,
    SeedIntegrityError,
    TenantResolutionError,
    hub_reset_boundary,
)
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


# --- project stamping -----------------------------------------------------------

_PROJECT_A = "proj_01HK153X00AAAAAAAAAAAAAAAA"
_PROJECT_A2 = "proj_01HK153X00CCCCCCCCCCCCCCCC"
_PROJECT_B = "proj_01HK153X00BBBBBBBBBBBBBBBB"


def _projected_hub_store(tmp_path: Path, projects: dict[str, list[str]]) -> tuple[str, MetaData]:
    """``_tenanted_hub_store`` once projects land: a tenant-owned ``projects`` table
    holding ``projects`` (tenant -> project ids, the first slugged ``default``), a
    ``chunks`` table whose ``project_id`` is required, a ``config_changes`` table whose
    ``project_id`` is nullable, and ``runner_registrations`` carrying its nullable
    served-projects declaration."""
    url, _tenanted = _tenanted_hub_store(tmp_path, *projects)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE runner_registrations ADD COLUMN projects TEXT"))
    meta = MetaData()
    meta.reflect(bind=engine)
    project_table = Table(
        "projects",
        meta,
        Column("project_id", String, primary_key=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("slug", String, nullable=False),
        Column("name", String, nullable=False),
    )
    Table(
        "chunks",
        meta,
        Column("chunk_id", String, primary_key=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("project_id", String, ForeignKey("projects.project_id"), nullable=False),
    )
    Table(
        "config_changes",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("project_id", String, ForeignKey("projects.project_id"), nullable=True),
    )
    meta.create_all(engine)
    with engine.begin() as conn:
        for tenant_id, project_ids in projects.items():
            for index, project_id in enumerate(project_ids):
                slug = "default" if index == 0 else f"p{index}"
                conn.execute(
                    project_table.insert().values(
                        project_id=project_id, tenant_id=tenant_id, slug=slug, name=slug.title()
                    )
                )
    return url, meta


def _chunk(chunk_id: str, **extra: object) -> FactRow:
    return FactRow(table="chunks", values={"chunk_id": chunk_id, **extra})


def test_write_stamps_the_tenants_only_project_where_one_is_required(tmp_path: Path) -> None:
    """A row of a table whose ``project_id`` is required lands in its tenant's only
    project — never another tenant's; a nullable ``project_id`` left out stays NULL."""
    url, _meta = _projected_hub_store(tmp_path, {_TENANT_A: [_PROJECT_A], _TENANT_B: [_PROJECT_B]})
    _store(url, tenant=_TENANT_A).write([_chunk("c1"), FactRow(table="config_changes", values={})])
    assert _rows(url, "chunks", "chunk_id", "tenant_id", "project_id") == [("c1", _TENANT_A, _PROJECT_A)]
    assert _rows(url, "config_changes", "tenant_id", "project_id") == [(_TENANT_A, None)]


def test_write_keeps_a_project_the_row_already_carries(tmp_path: Path) -> None:
    """A composer that names its project is never second-guessed — not even in a tenant
    of several projects, where the store could not pick one itself."""
    url, _meta = _projected_hub_store(tmp_path, {_TENANT_A: [_PROJECT_A, _PROJECT_A2]})
    _store(url).write([_chunk("c1", project_id=_PROJECT_A2)])
    assert _rows(url, "chunks", "chunk_id", "project_id") == [("c1", _PROJECT_A2)]


def test_write_refuses_a_tenant_of_several_projects_when_the_row_names_none(tmp_path: Path) -> None:
    url, _meta = _projected_hub_store(tmp_path, {_TENANT_A: [_PROJECT_A, _PROJECT_A2]})
    with pytest.raises(ProjectResolutionError) as excinfo:
        _store(url).write([_registration("r1"), _chunk("c1")])
    message = str(excinfo.value)
    assert "2 projects" in message
    assert _PROJECT_A in message and _PROJECT_A2 in message and "default" in message
    assert _rows(url, "chunks", "chunk_id") == []
    assert _rows(url, "runner_registrations", "runner_id") == []


def test_write_refuses_a_tenant_holding_no_project(tmp_path: Path) -> None:
    url, _meta = _projected_hub_store(tmp_path, {_TENANT_A: []})
    with pytest.raises(ProjectResolutionError, match="holds no project"):
        _store(url).write([_chunk("c1")])


def test_write_declares_a_seeded_runner_serves_the_tenants_project(tmp_path: Path) -> None:
    """A seeded runner registration declares it serves the tenant's project, in the very
    JSON the hub's migration gives a runner that declared nothing: one entry naming the
    project by its id, resolved to it."""
    url, _meta = _projected_hub_store(tmp_path, {_TENANT_A: [_PROJECT_A]})
    _store(url).write([_registration("r1")])
    [(declaration,)] = _rows(url, "runner_registrations", "projects")
    assert declaration == f'[{{"entry": "{_PROJECT_A}", "project_id": "{_PROJECT_A}"}}]'
    assert json.loads(str(declaration)) == [{"entry": _PROJECT_A, "project_id": _PROJECT_A}]


def test_write_keeps_a_declaration_the_registration_already_carries(tmp_path: Path) -> None:
    """A composed declaration wins, ``None`` included — a runner serving nothing — and
    composing every one leaves no project to resolve, so a tenant of several is fine."""
    url, _meta = _projected_hub_store(tmp_path, {_TENANT_A: [_PROJECT_A, _PROJECT_A2]})
    _store(url).write([_registration("r1", projects=None), _registration("r2", projects="[]")])
    assert _rows(url, "runner_registrations", "runner_id", "projects") == [("r1", None), ("r2", "[]")]


def test_write_without_project_columns_resolves_no_project(tmp_path: Path) -> None:
    """A store from before projects — no ``projects`` table, no project column — is
    written as it always was, with no project looked up."""
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A)
    _store(url).write([_registration("r1")])
    with create_engine(url).begin() as conn:
        assert conn.execute(select(meta.tables["runner_registrations"].c.runner_id)).scalars().all() == ["r1"]


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


# --- reset keeps the tenant boundary ---------------------------------------------


def _tenant_boundary_store(tmp_path: Path) -> tuple[str, MetaData]:
    """``_tenanted_hub_store`` of two tenants, each holding the rows a tenant starts
    with — a ``default`` project and a ``work_item_sequence`` row — plus a second
    project, a registered runner, a signed-up user, and the migration stamp."""
    url, meta = _tenanted_hub_store(tmp_path, _TENANT_A, _TENANT_B)
    engine = create_engine(url)
    extra = MetaData()
    extra.reflect(bind=engine)
    projects = Table(
        "projects",
        extra,
        Column("project_id", String, primary_key=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("slug", String, nullable=False),
    )
    sequence = Table(
        "work_item_sequence",
        extra,
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), primary_key=True),
        Column("source", String, primary_key=True),
        Column("next_ref", Integer, nullable=False),
    )
    alembic = Table("alembic_version", extra, Column("version_num", String, primary_key=True))
    extra.create_all(engine)
    with engine.begin() as conn:
        for tenant_id in (_TENANT_A, _TENANT_B):
            conn.execute(projects.insert().values(project_id=f"proj_{tenant_id}", tenant_id=tenant_id, slug="default"))
            conn.execute(sequence.insert().values(tenant_id=tenant_id, source="hub", next_ref=7))
        conn.execute(projects.insert().values(project_id="proj_other", tenant_id=_TENANT_A, slug="other"))
        conn.execute(alembic.insert().values(version_num="some_revision"))
        conn.execute(meta.tables["users"].insert().values(user_id="usr_1", email="a@example.com"))
    _store(url, tenant=_TENANT_A).write([_registration("r1"), FactRow(table="scopes", values={"slug": "core"})])
    return url, extra


def _rows(url: str, table: str, *columns: str) -> list[tuple[object, ...]]:
    with create_engine(url).begin() as conn:
        return [tuple(row) for row in conn.execute(text(f"SELECT {', '.join(columns)} FROM {table} ORDER BY 1"))]


def test_hub_reset_keeps_each_tenants_starting_rows_and_the_global_tables(tmp_path: Path) -> None:
    url, _meta = _tenant_boundary_store(tmp_path)

    summary = _store(url).reset(hub_reset_boundary(HUB_GLOBAL_TABLES))

    assert _rows(url, "tenants", "tenant_id") == [(_TENANT_A,), (_TENANT_B,)]
    assert _rows(url, "projects", "project_id") == [(f"proj_{_TENANT_A}",), (f"proj_{_TENANT_B}",)]
    assert _rows(url, "work_item_sequence", "tenant_id", "next_ref") == [(_TENANT_A, 7), (_TENANT_B, 7)]
    assert _rows(url, "users", "user_id") == [("usr_1",)]
    assert _rows(url, "alembic_version", "version_num") == [("some_revision",)]
    assert _rows(url, "runner_registrations", "runner_id") == []
    assert _rows(url, "scopes", "scope_id") == []
    # the registered runner, the scope, and the other project
    assert summary.rows_deleted == 3
    assert summary.table_count == 4  # projects is deleted from, though its default rows stay
    assert summary.tables_kept == 3  # tenants, users, work_item_sequence


def test_hub_reset_keeps_memberships_hub_admins_and_the_hub_event_log(tmp_path: Path) -> None:
    """Who belongs to which tenant, who holds the hub administrator role, and the hub's own
    occurrences are global: a reset that emptied them would leave every tenant without its
    members and the hub without its administrators."""
    url, _meta = _tenant_boundary_store(tmp_path)
    engine = create_engine(url)
    global_meta = MetaData()
    global_meta.reflect(bind=engine, only=["tenants"])
    memberships = Table(
        "membership_facts",
        global_meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("tenant_id", String, ForeignKey("tenants.tenant_id"), nullable=False),
        Column("user_id", String, nullable=False),
        Column("role", String, nullable=False),
    )
    hub_admins = Table(
        "hub_admin_facts",
        global_meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", String, nullable=False),
        Column("granted", Integer, nullable=False),
    )
    hub_events = Table(
        "hub_event_log",
        global_meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("kind", String, nullable=False),
    )
    global_meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(memberships.insert().values(tenant_id=_TENANT_B, user_id="usr_1", role="admin"))
        conn.execute(hub_admins.insert().values(user_id="usr_1", granted=1))
        conn.execute(hub_events.insert().values(kind="hub-started"))

    _store(url).reset(hub_reset_boundary(HUB_GLOBAL_TABLES))

    assert _rows(url, "membership_facts", "tenant_id", "user_id") == [(_TENANT_B, "usr_1")]
    assert _rows(url, "hub_admin_facts", "user_id") == [("usr_1",)]
    assert _rows(url, "hub_event_log", "kind") == [("hub-started",)]


def test_hub_reset_leaves_the_store_seedable_by_its_tenant(tmp_path: Path) -> None:
    url, _meta = _tenant_boundary_store(tmp_path)
    _store(url).reset(hub_reset_boundary(HUB_GLOBAL_TABLES))
    _store(url, tenant=_TENANT_B).write([_registration("r2")])
    assert _rows(url, "runner_registrations", "runner_id", "tenant_id") == [("r2", _TENANT_B)]


def test_hub_reset_of_a_store_from_before_tenancy_empties_every_table(tmp_path: Path) -> None:
    """A hub without a ``tenants`` table has no tenant boundary — its global tables are
    emptied with the rest, as every reset did before tenancy."""
    url, registrations, _pause = _hub_store(tmp_path)
    engine = create_engine(url)
    meta = MetaData()
    users = Table("users", meta, Column("user_id", String, primary_key=True))
    meta.create_all(engine)
    with engine.begin() as conn:
        conn.execute(users.insert().values(user_id="usr_1"))
    _store(url).write([_registration("r1")])

    summary = _store(url).reset(hub_reset_boundary(HUB_GLOBAL_TABLES))

    assert summary.rows_deleted == 2
    assert summary.tables_kept == 0
    with engine.begin() as conn:
        assert conn.execute(select(registrations)).all() == []
        assert conn.execute(select(users)).all() == []


def test_default_reset_still_empties_every_table(tmp_path: Path) -> None:
    url, _meta = _tenant_boundary_store(tmp_path)
    summary = _store(url).reset()
    assert summary.tables_kept == 0
    for table in ("tenants", "projects", "work_item_sequence", "users", "runner_registrations"):
        assert _rows(url, table, "*") == []
    assert _rows(url, "alembic_version", "version_num") == [("some_revision",)]
