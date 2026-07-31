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
)

from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.schema_contract import SchemaDriftError
from blizzard_mock.mock_data.domain.seeding import SeedIntegrityError
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


def _store(url: str) -> ReflectedStore:
    return ReflectedStore(create_seed_engine(url))


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


# --- reset (unmigrated store) --------------------------------------------------


def test_reset_rejects_an_unmigrated_store(tmp_path: Path) -> None:
    url = f"sqlite:///{tmp_path / 'empty.db'}"
    create_engine(url).connect().close()  # create the file with zero tables
    with pytest.raises(SchemaDriftError, match="no tables"):
        _store(url).reset()
