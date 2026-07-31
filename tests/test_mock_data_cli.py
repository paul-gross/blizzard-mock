"""Unit + component coverage for the mock-data CLI (``blizzard-mock:unit-test``).

The surface (verbs, help, contract) is asserted, plus the implemented verbs —
``reset`` (reflection-based delete-all), ``create runner``, ``create graph``, and
``create chunk`` — exercised against a **real sqlite store** whose schema mirrors the
hub's own DDL. No ``blizzard`` import: the CLI reflects whatever schema it is pointed
at, so the test builds the tables itself. The still-stubbed ``fixture`` verbs are
pinned too.
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
    Text,
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


def _full_hub_store(tmp_path: Path) -> tuple[str, MetaData]:
    """A sqlite store mirroring the hub's chunk/graph/fact tables ``create graph``/
    ``create chunk`` write into — the subset ``blizzard/hub/store/schema.py`` declares
    that this phase's composers touch."""
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    Table(
        "graphs",
        meta,
        Column("graph_id", String, primary_key=True),
        Column("name", String, nullable=False),
        Column("entry_node_id", String, nullable=False),
        Column("definition_yaml", Text, nullable=False),
        Column("created_at", DateTime, nullable=False),
    )
    Table(
        "graph_nodes",
        meta,
        Column("node_id", String, primary_key=True),
        Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
        Column("name", String, nullable=False),
        Column("executor", String, nullable=False),
        Column("prompt", Text, nullable=True),
        Column("judgement_prompt", Text, nullable=True),
        Column("session", String, nullable=False),
        Column("session_source", String, nullable=True),
        Column("judged_by", String, nullable=False),
        Column("retries_max", Integer, nullable=True),
        Column("retries_exhausted", String, nullable=True),
        Column("mode", String, nullable=True),
        Column("produces", Text, nullable=True),
        Column("checks", Text, nullable=True),
        Column("checks_cwd", String, nullable=True),
        Column("checks_timeout", Integer, nullable=True),
        Column("bounce_cap", Integer, nullable=True),
        Column("run", Text, nullable=True),
        Column("poll_interval_seconds", Integer, nullable=True),
        Column("poll_timeout_seconds", Integer, nullable=True),
    )
    Table(
        "graph_choices",
        meta,
        Column("choice_id", String, primary_key=True),
        Column("node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
        Column("name", String, nullable=False),
        Column("description", Text, nullable=False),
        Column("requires_checks", Boolean, nullable=True),
    )
    Table(
        "graph_edges",
        meta,
        Column("edge_id", String, primary_key=True),
        Column("from_node_id", String, ForeignKey("graph_nodes.node_id"), nullable=False),
        Column("choice_id", String, ForeignKey("graph_choices.choice_id"), nullable=False),
        Column("to_node_name", String, nullable=False),
        Column("prompt_addendum", Text, nullable=True),
        Column("to_graph_model", String, nullable=True),
    )
    Table(
        "chunks",
        meta,
        Column("chunk_id", String, primary_key=True),
        Column("graph_id", String, ForeignKey("graphs.graph_id"), nullable=False),
        Column("minted_at", DateTime, nullable=False),
        Column("model", String, nullable=False),
        Column("default_model", Text, nullable=True),
        Column("default_effort", String, nullable=True),
        Column("intended_migration", Text, nullable=True),
    )
    Table(
        "chunk_work_refs",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("source", String, nullable=False),
        Column("ref", String, nullable=False),
    )
    Table(
        "chunk_promoted",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("promoted_at", DateTime, nullable=False),
    )
    Table(
        "chunk_stopped",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("stopped_at", DateTime, nullable=False),
        Column("stopped_by", String, nullable=True),
    )
    Table(
        "escalations",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("takeover_command", Text, nullable=False, server_default=""),
        Column("decision_id", String, nullable=True),
        Column("recorded_at", DateTime, nullable=False),
    )
    Table(
        "questions",
        meta,
        Column("question_id", String, primary_key=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("node_id", String, nullable=True),
        Column("session_id", String, nullable=True),
        Column("runner_id", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("question", Text, nullable=False),
        Column("options", Text, nullable=False),
        Column("asked_at", DateTime, nullable=False),
    )
    Table(
        "question_answers",
        meta,
        Column("question_id", String, ForeignKey("questions.question_id"), primary_key=True),
        Column("answer", Text, nullable=False),
        Column("answered_by", String, nullable=False),
        Column("answered_at", DateTime, nullable=False),
    )
    Table(
        "chunk_pause_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("paused", Boolean, nullable=False),
        Column("set_at", DateTime, nullable=False),
        Column("set_by", String, nullable=False),
    )
    Table(
        "transitions",
        meta,
        Column("transition_id", String, primary_key=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("graph_id", String, nullable=False),
        Column("from_node_id", String, nullable=True),
        Column("to_node_id", String, nullable=False),
        Column("choice_name", String, nullable=True),
        Column("decision_id", String, nullable=True),
        Column("epoch", Integer, nullable=False),
        Column("runner_id", String, nullable=False),
        Column("recorded_at", DateTime, nullable=False),
    )
    Table(
        "lease_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("runner_id", String, nullable=False),
        Column("minted_at", DateTime, nullable=False),
    )
    Table(
        "route_created",
        meta,
        Column("route_id", String, primary_key=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("runner_id", String, nullable=False),
        Column("workspace_id", String, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("seq", Integer, nullable=False),
    )
    Table(
        "route_released",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("released_at", DateTime, nullable=False),
        Column("seq", Integer, nullable=False),
    )
    meta.create_all(engine)
    return url, meta


def _table(meta: MetaData, name: str) -> Table:
    return meta.tables[name]


# --- surface ---------------------------------------------------------------


def test_root_help_describes_contract() -> None:
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for verb in ("reset", "create", "fixture"):
        assert verb in result.output


def test_verbs_expose_help() -> None:
    runner = _runner()
    for args in (
        ["reset", "--help"],
        ["create", "--help"],
        ["create", "runner", "--help"],
        ["create", "graph", "--help"],
        ["create", "chunk", "--help"],
        ["fixture", "--help"],
        ["fixture", "apply", "--help"],
    ):
        assert runner.invoke(cli, args).exit_code == 0, args


def test_create_group_help_lists_every_subcommand() -> None:
    result = _runner().invoke(cli, ["create", "--help"])
    assert result.exit_code == 0
    for subcommand in ("runner", "graph", "chunk"):
        assert subcommand in result.output


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


# --- create graph (implemented) ---------------------------------------------


def test_create_graph_mints_a_build_and_a_deliver_node(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "graph", "--store", "hub", "--url", url, "--name", "test-graph"])
    assert result.exit_code == 0, result.output
    graph_id = result.output.strip()
    assert graph_id

    with create_engine(url).begin() as conn:
        graph_rows = conn.execute(select(_table(meta, "graphs"))).all()
        node_rows = conn.execute(select(_table(meta, "graph_nodes"))).all()
        choice_rows = conn.execute(select(_table(meta, "graph_choices"))).all()
        edge_rows = conn.execute(select(_table(meta, "graph_edges"))).all()
    assert [r.graph_id for r in graph_rows] == [graph_id]
    assert graph_rows[0].name == "test-graph"
    assert sorted(r.executor for r in node_rows) == ["hub", "runner"]
    assert len(choice_rows) == 2
    assert len(edge_rows) == 2


# --- create chunk (implemented) ----------------------------------------------


def test_create_chunk_auto_mints_a_graph_when_the_store_has_none(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"])
    assert result.exit_code == 0, result.output
    chunk_id = result.output.strip()
    assert chunk_id

    with create_engine(url).begin() as conn:
        graph_rows = conn.execute(select(_table(meta, "graphs"))).all()
        chunk_rows = conn.execute(select(_table(meta, "chunks"))).all()
        promoted_rows = conn.execute(select(_table(meta, "chunk_promoted"))).all()
    assert len(graph_rows) == 1
    assert [r.chunk_id for r in chunk_rows] == [chunk_id]
    assert [r.chunk_id for r in promoted_rows] == [chunk_id]


def test_create_chunk_reuses_a_named_graph_instead_of_minting_another(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    graph_result = runner.invoke(cli, ["create", "graph", "--store", "hub", "--url", url, "--name", "reuse-me"])
    assert graph_result.exit_code == 0, graph_result.output
    graph_id = graph_result.output.strip()

    chunk_result = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running", "--graph", "reuse-me"]
    )
    assert chunk_result.exit_code == 0, chunk_result.output

    with create_engine(url).begin() as conn:
        graph_rows = conn.execute(select(_table(meta, "graphs"))).all()
        chunk_rows = conn.execute(select(_table(meta, "chunks"))).all()
    assert [r.graph_id for r in graph_rows] == [graph_id]
    assert chunk_rows[0].graph_id == graph_id


def test_create_chunk_lands_work_refs(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "chunk",
            "--store",
            "hub",
            "--url",
            url,
            "--status",
            "ready",
            "--work-ref",
            "gh#1",
            "--work-ref",
            "gh#2",
        ],
    )
    assert result.exit_code == 0, result.output
    chunk_id = result.output.strip()
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "chunk_work_refs"))).all()
    assert sorted((r.chunk_id, r.source, r.ref) for r in rows) == [(chunk_id, "gh", "1"), (chunk_id, "gh", "2")]


def test_create_chunk_prints_only_the_chunk_id(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"])
    assert result.exit_code == 0, result.output
    assert len(result.output.strip().splitlines()) == 1


_STATUS_EXPECTATIONS = {
    "stopped": "chunk_stopped",
    "done": "transitions",
    "needs_human": "escalations",
    "waiting_on_human": "questions",
    "paused": "chunk_pause_facts",
    "delivering": "route_created",
    "running": "route_created",
    "not_ready": None,
    "ready": "chunk_promoted",
}


def test_create_chunk_lands_the_right_fact_table_per_status(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    for status, expected_table in _STATUS_EXPECTATIONS.items():
        result = runner.invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", status])
        assert result.exit_code == 0, (status, result.output)
        chunk_id = result.output.strip()
        if expected_table is None:
            continue
        with create_engine(url).begin() as conn:
            rows = conn.execute(
                select(_table(meta, expected_table)).where(_table(meta, expected_table).c.chunk_id == chunk_id)
            ).all()
        assert rows, (status, expected_table)


def test_create_chunk_done_reaches_the_reserved_terminal(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "done"])
    assert result.exit_code == 0, result.output
    chunk_id = result.output.strip()
    with create_engine(url).begin() as conn:
        transitions = _table(meta, "transitions")
        rows = conn.execute(select(transitions.c.to_node_id).where(transitions.c.chunk_id == chunk_id)).all()
    assert [r.to_node_id for r in rows] == ["done"]


def test_create_chunk_delivering_transitions_into_the_hub_node(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "delivering"])
    assert result.exit_code == 0, result.output
    chunk_id = result.output.strip()
    with create_engine(url).begin() as conn:
        transitions = _table(meta, "transitions")
        nodes = _table(meta, "graph_nodes")
        to_node_id = conn.execute(select(transitions.c.to_node_id).where(transitions.c.chunk_id == chunk_id)).scalar()
        executor = conn.execute(select(nodes.c.executor).where(nodes.c.node_id == to_node_id)).scalar()
    assert executor == "hub"


def test_create_chunk_running_transitions_into_a_non_hub_node(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"])
    assert result.exit_code == 0, result.output
    chunk_id = result.output.strip()
    with create_engine(url).begin() as conn:
        transitions = _table(meta, "transitions")
        nodes = _table(meta, "graph_nodes")
        to_node_id = conn.execute(select(transitions.c.to_node_id).where(transitions.c.chunk_id == chunk_id)).scalar()
        executor = conn.execute(select(nodes.c.executor).where(nodes.c.node_id == to_node_id)).scalar()
    assert executor == "runner"


def test_create_chunk_delivering_refuses_a_non_hub_node(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "delivering", "--node", "build"]
    )
    assert result.exit_code == 1
    assert "hub-executor" in result.output


def test_create_chunk_running_refuses_a_hub_node(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running", "--node", "deliver"]
    )
    assert result.exit_code == 1
    assert "hub-executed" in result.output


def test_create_chunk_ready_refuses_a_node(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready", "--node", "build"]
    )
    assert result.exit_code == 1
    assert "mints no transition" in result.output


# --- still-stubbed verbs ----------------------------------------------------


def test_create_unknown_subcommand_is_clicks_own_no_such_command(tmp_path: Path) -> None:
    url, *_ = _hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "bogus", "--store", "hub", "--url", url])
    assert result.exit_code == 2
    assert "No such command" in result.output


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
