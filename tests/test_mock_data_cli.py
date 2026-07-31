"""Unit + component coverage for the mock-data CLI (``blizzard-mock:unit-test``).

The surface (verbs, help, contract) is asserted, plus the two implemented verbs —
``reset`` (reflection-based delete-all) and ``create runner`` — exercised against a
**real sqlite store** whose schema mirrors the hub's fleet-registry DDL. No ``blizzard``
import: the CLI reflects whatever schema it is pointed at, so the test builds the tables
itself. The still-stubbed verbs are pinned too.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner
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
    insert,
    select,
)

from blizzard_mock.mock_data.cli import cli


def _runner() -> CliRunner:
    return CliRunner()


def _hub_store(tmp_path: Path) -> tuple[str, MetaData, Table, Table]:
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
    return url, meta, registrations, pause_facts


# --- surface ---------------------------------------------------------------


def test_root_help_describes_contract() -> None:
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for verb in ("reset", "create", "fixture"):
        assert verb in result.output


def test_verbs_expose_help() -> None:
    runner = _runner()
    for args in (["reset", "--help"], ["create", "--help"], ["fixture", "--help"], ["fixture", "apply", "--help"]):
        assert runner.invoke(cli, args).exit_code == 0, args


# --- reset (implemented) ---------------------------------------------------


def test_reset_clears_all_rows(tmp_path: Path) -> None:
    url, _meta, registrations, pause_facts = _hub_store(tmp_path)
    engine = create_engine(url)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(registrations).values(runner_id="r1", workspace_id="ws", registered_at=now, last_seen_at=now)
        )
        conn.execute(insert(pause_facts).values(runner_id="r1", paused=True, set_at=now, set_by="t"))

    result = _runner().invoke(cli, ["reset", "--store", "hub", "--url", url])
    assert result.exit_code == 0, result.output
    assert "cleared 2 row(s)" in result.output
    with engine.begin() as conn:
        assert conn.execute(select(registrations)).all() == []
        assert conn.execute(select(pause_facts)).all() == []


def test_reset_requires_a_url() -> None:
    result = _runner().invoke(cli, ["reset", "--store", "hub"], env={"DATABASE_URL": ""})
    assert result.exit_code != 0
    assert "no store URL" in result.output


# --- create runner (implemented) -------------------------------------------


def test_create_runner_seeds_the_registry(tmp_path: Path) -> None:
    url, _meta, registrations, _pause = _hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "runner", "--store", "hub", "--url", url, "--runner-id", "seeded-1"])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(registrations.c.runner_id)).all()
    assert [r[0] for r in rows] == ["seeded-1"]


def test_create_runner_paused_lands_a_pause_fact(tmp_path: Path) -> None:
    url, _meta, _reg, pause_facts = _hub_store(tmp_path)
    result = _runner().invoke(
        cli, ["create", "runner", "--store", "hub", "--url", url, "--runner-id", "seeded-2", "--paused"]
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(pause_facts.c.runner_id, pause_facts.c.paused)).all()
    assert rows == [("seeded-2", True)]


# --- still-stubbed verbs ----------------------------------------------------


def test_create_unknown_model_is_stub(tmp_path: Path) -> None:
    url, *_ = _hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url])
    assert result.exit_code == 1
    assert "not implemented" in result.output


def test_fixture_apply_is_stub() -> None:
    result = _runner().invoke(cli, ["fixture", "apply", "parked-on-question"])
    assert result.exit_code == 1
    assert "not implemented" in result.output


# --- schema drift surfaces as a clean CLI error -----------------------------


def test_create_runner_against_a_drifted_schema_is_a_clean_click_exception(tmp_path: Path) -> None:
    """A table missing the columns ``create runner`` supplies fails as a ``ClickException``
    naming the drift — not a raw SQLAlchemy traceback."""
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    # `runner_registrations` exists but is missing `workspace_id` — a schema drift.
    Table("runner_registrations", meta, Column("runner_id", String, primary_key=True))
    meta.create_all(engine)

    result = _runner().invoke(cli, ["create", "runner", "--store", "hub", "--url", url, "--runner-id", "r1"])
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "runner_registrations" in result.output
    assert "workspace_id" in result.output
    assert "Traceback" not in result.output


# --- --dir resolves a runtime config's db_url -------------------------------


def test_reset_accepts_a_runtime_dir_in_place_of_url(tmp_path: Path) -> None:
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    url, *_ = _hub_store(store_dir)
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "blizzard-hub.toml").write_text(f'db_url = "{url}"\n')

    result = _runner().invoke(cli, ["reset", "--store", "hub", "--dir", str(runtime_dir)])
    assert result.exit_code == 0, result.output
    assert "cleared 0 row(s)" in result.output
