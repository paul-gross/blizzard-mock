"""Unit + component coverage for the mock-data CLI (``blizzard-mock:unit-test``).

The surface (verbs, help, contract) is asserted, plus the implemented verbs —
``reset`` (reflection-based delete-all), ``create runner``, ``create graph``, and
``create chunk`` — exercised against a **real sqlite store** whose schema mirrors the
hub's own DDL. No ``blizzard`` import: the CLI reflects whatever schema it is pointed
at, so the test builds the tables itself. The still-stubbed ``fixture`` verbs are
pinned too.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from click.testing import CliRunner
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
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
    Table(
        "usage_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("node_id", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("runner_id", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("model", String, nullable=False),
        Column("input_tokens", Integer, nullable=False),
        Column("output_tokens", Integer, nullable=False),
        Column("cache_read_tokens", Integer, nullable=False),
        Column("cache_create_tokens", Integer, nullable=False),
        Column("cost_usd", Float, nullable=True),
        Column("recorded_at", DateTime, nullable=False),
    )
    Table(
        "answer_deliveries",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("question_id", String, ForeignKey("questions.question_id"), nullable=False),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("delivered_at", DateTime, nullable=False),
    )
    Table(
        "runner_registrations",
        meta,
        Column("runner_id", String, primary_key=True),
        Column("workspace_id", String, nullable=False),
        Column("registered_at", DateTime, nullable=False),
        Column("last_seen_at", DateTime, nullable=False),
    )
    Table(
        "runner_pause_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("runner_id", String, ForeignKey("runner_registrations.runner_id"), nullable=False),
        Column("paused", Boolean, nullable=False),
        Column("set_at", DateTime, nullable=False),
        Column("set_by", String, nullable=False),
    )
    Table(
        "runner_local_pause_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("runner_id", String, nullable=False),
        Column("paused", Boolean, nullable=False),
        Column("set_at", DateTime, nullable=False),
        Column("set_by", String, nullable=False),
        Column("reason", Text, nullable=True),
    )
    Table(
        "event_log",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("recorded_at", DateTime, nullable=False),
        Column("severity", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("runner_id", String, nullable=False),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=True),
        Column("lease_id", String, nullable=True),
        Column("node_name", String, nullable=True),
        Column("message", Text, nullable=False),
        Column("detail", Text, nullable=True),
    )
    meta.create_all(engine)
    return url, meta


def _table(meta: MetaData, name: str) -> Table:
    return meta.tables[name]


# --- surface ---------------------------------------------------------------


def test_root_help_describes_contract() -> None:
    result = _runner().invoke(cli, ["--help"])
    assert result.exit_code == 0
    for verb in ("reset", "create", "scenario", "fixture"):
        assert verb in result.output


def test_verbs_expose_help() -> None:
    runner = _runner()
    for args in (
        ["reset", "--help"],
        ["create", "--help"],
        ["create", "runner", "--help"],
        ["create", "graph", "--help"],
        ["create", "chunk", "--help"],
        ["create", "usage", "--help"],
        ["create", "lease", "--help"],
        ["create", "escalation", "--help"],
        ["create", "question", "--help"],
        ["create", "event", "--help"],
        ["create", "runner-pause", "--help"],
        ["scenario", "--help"],
        ["scenario", "board", "--help"],
        ["fixture", "--help"],
        ["fixture", "apply", "--help"],
    ):
        assert runner.invoke(cli, args).exit_code == 0, args


def test_create_group_help_lists_every_subcommand() -> None:
    result = _runner().invoke(cli, ["create", "--help"])
    assert result.exit_code == 0
    for subcommand in ("runner", "graph", "chunk", "usage", "lease", "escalation", "question", "event", "runner-pause"):
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


# --- create usage (implemented) ---------------------------------------------


def test_create_usage_lands_a_cost_fact(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_result = runner.invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"])
    assert chunk_result.exit_code == 0, chunk_result.output
    chunk_id = chunk_result.output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--kind",
            "spawn",
            "--model",
            "claude-x",
            "--input-tokens",
            "100",
            "--output-tokens",
            "50",
            "--cost-usd",
            "1.23",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(
            select(_table(meta, "usage_facts")).where(_table(meta, "usage_facts").c.chunk_id == chunk_id)
        ).all()
    assert len(rows) == 1
    assert rows[0].cost_usd == 1.23
    assert rows[0].input_tokens == 100
    assert rows[0].output_tokens == 50


def test_create_usage_no_cost_lands_a_genuine_null(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--kind",
            "resume",
            "--model",
            "claude-x",
            "--input-tokens",
            "10",
            "--no-cost",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        cost = conn.execute(
            select(_table(meta, "usage_facts").c.cost_usd).where(_table(meta, "usage_facts").c.chunk_id == chunk_id)
        ).scalar()
    assert cost is None


def test_create_usage_requires_exactly_one_of_cost_flags(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()
    base = [
        "create",
        "usage",
        "--store",
        "hub",
        "--url",
        url,
        "--chunk",
        chunk_id,
        "--kind",
        "spawn",
        "--model",
        "claude-x",
        "--input-tokens",
        "1",
    ]
    neither = runner.invoke(cli, base)
    assert neither.exit_code != 0
    assert "exactly one" in neither.output
    both = runner.invoke(cli, [*base, "--cost-usd", "1.0", "--no-cost"])
    assert both.exit_code != 0
    assert "mutually exclusive" in both.output


def test_create_usage_defaults_node_epoch_runner_from_the_chunks_lease_and_transition(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli,
        [
            "create",
            "chunk",
            "--store",
            "hub",
            "--url",
            url,
            "--status",
            "running",
            "--runner-id",
            "r-usage",
            "--epoch",
            "3",
        ],
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--kind",
            "judge",
            "--model",
            "claude-x",
            "--input-tokens",
            "1",
            "--no-cost",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        row = conn.execute(
            select(_table(meta, "usage_facts")).where(_table(meta, "usage_facts").c.chunk_id == chunk_id)
        ).one()
        transitions = _table(meta, "transitions")
        newest_node = conn.execute(select(transitions.c.to_node_id).where(transitions.c.chunk_id == chunk_id)).scalar()
    assert row.epoch == 3
    assert row.runner_id == "r-usage"
    assert row.node_id == newest_node


def test_create_usage_refuses_when_no_node_is_derivable(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()
    result = runner.invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--kind",
            "spawn",
            "--model",
            "claude-x",
            "--input-tokens",
            "1",
            "--no-cost",
        ],
    )
    assert result.exit_code != 0
    assert "--node" in result.output


# --- create lease (implemented) ----------------------------------------------


def test_create_lease_lands_a_lease_fact(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "lease",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--runner-id",
            "r-lease",
            "--epoch",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(
            select(_table(meta, "lease_facts")).where(_table(meta, "lease_facts").c.chunk_id == chunk_id)
        ).all()
    assert [(r.runner_id, r.epoch) for r in rows] == [("r-lease", 5)]


# --- create escalation (implemented) -----------------------------------------


def test_create_escalation_default_cause_composes_the_generic_placeholder(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(cli, ["create", "escalation", "--store", "hub", "--url", url, "--chunk", chunk_id])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        takeover = conn.execute(
            select(_table(meta, "escalations").c.takeover_command).where(
                _table(meta, "escalations").c.chunk_id == chunk_id
            )
        ).scalar()
    assert takeover == f"cd <workdir> && <resume {chunk_id}>"


def test_create_escalation_cause_cap_composes_recognizable_spend_cap_wording(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli, ["create", "escalation", "--store", "hub", "--url", url, "--chunk", chunk_id, "--cause", "cap"]
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        takeover = conn.execute(
            select(_table(meta, "escalations").c.takeover_command).where(
                _table(meta, "escalations").c.chunk_id == chunk_id
            )
        ).scalar()
    assert takeover is not None
    assert takeover.startswith("spend cap ")
    assert "reached" in takeover
    assert takeover.endswith(f"cd <workdir> && <resume {chunk_id}>")


def test_create_escalation_explicit_takeover_command_overrides_the_cause_default(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "escalation",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--cause",
            "cap",
            "--takeover-command",
            "cd /custom && claude --resume abc",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        takeover = conn.execute(
            select(_table(meta, "escalations").c.takeover_command).where(
                _table(meta, "escalations").c.chunk_id == chunk_id
            )
        ).scalar()
    assert takeover == "cd /custom && claude --resume abc"


# --- create question (implemented) -------------------------------------------


def test_create_question_lands_an_open_question(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "question",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--text",
            "which way?",
            "--option",
            "a",
            "--option",
            "b",
        ],
    )
    assert result.exit_code == 0, result.output
    question_id = result.output.strip()
    assert question_id.startswith("qn_")
    with create_engine(url).begin() as conn:
        question_rows = conn.execute(select(_table(meta, "questions"))).all()
        answer_rows = conn.execute(select(_table(meta, "question_answers"))).all()
    assert [q.question_id for q in question_rows] == [question_id]
    assert question_rows[0].options == '["a", "b"]'
    assert answer_rows == []


def test_create_question_with_answer_lands_the_answer_row(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "question",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--text",
            "which way?",
            "--answer",
            "left",
            "--answered-by",
            "operator-1",
        ],
    )
    assert result.exit_code == 0, result.output
    question_id = result.output.strip()
    with create_engine(url).begin() as conn:
        answer_rows = conn.execute(select(_table(meta, "question_answers"))).all()
    assert [(r.question_id, r.answer, r.answered_by) for r in answer_rows] == [(question_id, "left", "operator-1")]


def test_create_question_delivered_lands_the_delivery_row(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "question",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--text",
            "which way?",
            "--answer",
            "left",
            "--answered-by",
            "operator-1",
            "--delivered",
        ],
    )
    assert result.exit_code == 0, result.output
    question_id = result.output.strip()
    with create_engine(url).begin() as conn:
        delivery_rows = conn.execute(select(_table(meta, "answer_deliveries"))).all()
    assert [(r.question_id, r.chunk_id) for r in delivery_rows] == [(question_id, chunk_id)]


def test_create_question_twice_on_the_same_chunk_lands_two_independent_trails(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    first = runner.invoke(
        cli, ["create", "question", "--store", "hub", "--url", url, "--chunk", chunk_id, "--text", "q1"]
    )
    second = runner.invoke(
        cli, ["create", "question", "--store", "hub", "--url", url, "--chunk", chunk_id, "--text", "q2"]
    )
    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    first_id, second_id = first.output.strip(), second.output.strip()
    assert first_id != second_id
    with create_engine(url).begin() as conn:
        rows = conn.execute(
            select(_table(meta, "questions").c.question_id).where(_table(meta, "questions").c.chunk_id == chunk_id)
        ).all()
    assert {r.question_id for r in rows} == {first_id, second_id}


def test_create_question_delivered_without_answer_is_refused(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli, ["create", "question", "--store", "hub", "--url", url, "--chunk", chunk_id, "--text", "q", "--delivered"]
    )
    assert result.exit_code != 0
    assert "--answer" in result.output


def test_create_question_answer_without_answered_by_is_refused(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        ["create", "question", "--store", "hub", "--url", url, "--chunk", chunk_id, "--text", "q", "--answer", "left"],
    )
    assert result.exit_code != 0
    assert "--answered-by" in result.output


def test_create_question_resumed_without_delivered_is_refused(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli, ["create", "question", "--store", "hub", "--url", url, "--chunk", chunk_id, "--text", "q", "--resumed"]
    )
    assert result.exit_code != 0
    assert "--resumed" in result.output


# --- create event (implemented) -----------------------------------------------


def test_create_event_lands_a_row(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "event",
            "--store",
            "hub",
            "--url",
            url,
            "--kind",
            "runner.registered",
            "--severity",
            "info",
            "--message",
            "hello",
            "--runner-id",
            "r-1",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "event_log"))).all()
    assert len(rows) == 1
    assert rows[0].runner_id == "r-1"
    assert rows[0].severity == "info"


def test_create_event_with_chunk_defaults_runner_id_from_the_chunks_newest_lease(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running", "--runner-id", "r-lease-holder"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "event",
            "--store",
            "hub",
            "--url",
            url,
            "--kind",
            "chunk.noticed",
            "--severity",
            "warning",
            "--message",
            "hi",
            "--chunk",
            chunk_id,
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        runner_id = conn.execute(
            select(_table(meta, "event_log").c.runner_id).where(_table(meta, "event_log").c.chunk_id == chunk_id)
        ).scalar()
    assert runner_id == "r-lease-holder"


def test_create_event_with_chunk_and_no_lease_falls_back_to_the_placeholder(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "event",
            "--store",
            "hub",
            "--url",
            url,
            "--kind",
            "chunk.noticed",
            "--severity",
            "info",
            "--message",
            "hi",
            "--chunk",
            chunk_id,
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        runner_id = conn.execute(
            select(_table(meta, "event_log").c.runner_id).where(_table(meta, "event_log").c.chunk_id == chunk_id)
        ).scalar()
    assert runner_id == "mock-data"


def test_create_event_detail_must_parse_as_json(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "event",
            "--store",
            "hub",
            "--url",
            url,
            "--kind",
            "chunk.noticed",
            "--severity",
            "info",
            "--message",
            "hi",
            "--runner-id",
            "r-1",
            "--detail",
            "{not json",
        ],
    )
    assert result.exit_code != 0
    assert "JSON" in result.output


# --- create runner-pause (implemented) ----------------------------------------


def test_create_runner_pause_local_lands_a_reason(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "runner-pause",
            "--store",
            "hub",
            "--url",
            url,
            "--runner-id",
            "r-1",
            "--local",
            "--reason",
            "ceiling",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "runner_local_pause_facts"))).all()
    assert [(r.runner_id, r.paused, r.reason) for r in rows] == [("r-1", True, "ceiling")]


def test_create_runner_pause_fleet_lands_no_reason_column(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli, ["create", "runner-pause", "--store", "hub", "--url", url, "--runner-id", "r-1", "--fleet"]
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "runner_pause_facts"))).all()
    assert [(r.runner_id, r.paused) for r in rows] == [("r-1", True)]


def test_create_runner_pause_fleet_with_reason_fails_loud(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        ["create", "runner-pause", "--store", "hub", "--url", url, "--runner-id", "r-1", "--fleet", "--reason", "oops"],
    )
    assert result.exit_code != 0
    assert "runner_pause_facts" in result.output
    assert "reason" in result.output


def test_create_runner_pause_requires_exactly_one_of_local_fleet(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    neither = runner.invoke(cli, ["create", "runner-pause", "--store", "hub", "--url", url, "--runner-id", "r-1"])
    assert neither.exit_code != 0
    assert "exactly one" in neither.output
    both = runner.invoke(
        cli, ["create", "runner-pause", "--store", "hub", "--url", url, "--runner-id", "r-1", "--local", "--fleet"]
    )
    assert both.exit_code != 0
    assert "exactly one" in both.output


# --- scenario board (implemented) --------------------------------------------

_SCENARIO_CHUNK_LINE = re.compile(r"^\s*chunk (\S+) status=(\S+)$", re.MULTILINE)


def _scenario_chunk_statuses(output: str) -> list[tuple[str, str]]:
    return _SCENARIO_CHUNK_LINE.findall(output)


def test_scenario_board_seeds_the_deterministic_six_chunk_census(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert result.exit_code == 0, result.output

    pairs = _scenario_chunk_statuses(result.output)
    assert [status for _chunk_id, status in pairs] == [
        "ready",
        "running",
        "needs_human",
        "waiting_on_human",
        "done",
        "paused",
    ]
    assert "stress extras: not included" in result.output

    with create_engine(url).begin() as conn:
        chunk_rows = conn.execute(select(_table(meta, "chunks"))).all()
    assert len(chunk_rows) == 6

    for chunk_id, status in pairs:
        expected_table = _STATUS_EXPECTATIONS[status]
        assert expected_table is not None, status
        with create_engine(url).begin() as conn:
            rows = conn.execute(
                select(_table(meta, expected_table)).where(_table(meta, expected_table).c.chunk_id == chunk_id)
            ).all()
        assert rows, (chunk_id, status)


def test_scenario_board_default_chunk_count_is_six(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        chunk_rows = conn.execute(select(_table(meta, "chunks"))).all()
    assert len(chunk_rows) == 6


def test_scenario_board_lands_at_least_one_cost_partial_usage_fact(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        usage_rows = conn.execute(select(_table(meta, "usage_facts"))).all()
    assert len(usage_rows) >= 2
    assert any(row.cost_usd is None for row in usage_rows)
    assert any(row.cost_usd is not None for row in usage_rows)


def test_scenario_board_lands_a_ceiling_paused_runner_with_a_reason(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        pause_rows = conn.execute(select(_table(meta, "runner_local_pause_facts"))).all()
    assert len(pause_rows) == 1
    assert pause_rows[0].paused is True
    assert pause_rows[0].reason
    assert "ceiling" in pause_rows[0].reason


def test_scenario_board_registers_a_runner_per_chunk(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        registration_rows = conn.execute(select(_table(meta, "runner_registrations"))).all()
    assert len(registration_rows) == 6


def test_scenario_board_populates_a_mixed_event_log(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        event_rows = conn.execute(select(_table(meta, "event_log"))).all()
    assert len(event_rows) >= 3
    assert {row.severity for row in event_rows} >= {"info", "warning", "critical"}


def test_scenario_board_nine_or_more_chunks_covers_every_status(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "9", "--seed", "1"])
    assert result.exit_code == 0, result.output
    pairs = _scenario_chunk_statuses(result.output)
    assert {status for _chunk_id, status in pairs} == set(_STATUS_EXPECTATIONS)


def test_scenario_board_stress_adds_the_four_extremes(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1", "--stress"])
    assert result.exit_code == 0, result.output
    assert "stress extras: included" in result.output

    with create_engine(url).begin() as conn:
        registration_rows = conn.execute(select(_table(meta, "runner_registrations"))).all()
        node_rows = conn.execute(select(_table(meta, "graph_nodes"))).all()
        question_rows = conn.execute(select(_table(meta, "questions"))).all()

    # 1. a runner with a long identity
    assert any(len(row.runner_id) > 100 for row in registration_rows)
    # 2. an extra waiting_on_human chunk (base six already carries one at --chunks 6)
    pairs = _scenario_chunk_statuses(result.output)
    assert [status for _chunk_id, status in pairs].count("waiting_on_human") == 2
    # 3. a chunk landed on a deliberately long custom node name
    long_nodes = [row for row in node_rows if len(row.name) > 100]
    assert long_nodes
    # 4. a multi-question chunk: 2+ distinct question ids under one chunk_id
    chunk_ids = [row.chunk_id for row in question_rows]
    assert any(chunk_ids.count(chunk_id) >= 2 for chunk_id in set(chunk_ids))


def test_scenario_board_without_stress_omits_the_extras(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert result.exit_code == 0, result.output
    assert "stress extras: not included" in result.output
    with create_engine(url).begin() as conn:
        registration_rows = conn.execute(select(_table(meta, "runner_registrations"))).all()
    assert len(registration_rows) == 6
    assert all(len(row.runner_id) < 100 for row in registration_rows)


def test_scenario_board_same_seed_is_byte_identical_across_two_fresh_stores(tmp_path: Path) -> None:
    store_a, store_b = tmp_path / "a", tmp_path / "b"
    store_a.mkdir()
    store_b.mkdir()
    url_a, meta_a = _full_hub_store(store_a)
    url_b, meta_b = _full_hub_store(store_b)
    result_a = _runner().invoke(cli, ["scenario", "board", "--url", url_a, "--chunks", "6", "--seed", "7", "--stress"])
    result_b = _runner().invoke(cli, ["scenario", "board", "--url", url_b, "--chunks", "6", "--seed", "7", "--stress"])
    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output
    # every line matches except the "seeded into ... <url>" line, which necessarily
    # names each store's own path.
    lines_a, lines_b = result_a.output.splitlines(), result_b.output.splitlines()
    assert len(lines_a) == len(lines_b)
    for line_a, line_b in zip(lines_a, lines_b, strict=True):
        if line_a.startswith("scenario board seeded into"):
            continue
        assert line_a == line_b

    with create_engine(url_a).begin() as conn:
        chunk_ids_a = sorted(r.chunk_id for r in conn.execute(select(_table(meta_a, "chunks"))).all())
        graph_ids_a = sorted(r.graph_id for r in conn.execute(select(_table(meta_a, "graphs"))).all())
    with create_engine(url_b).begin() as conn:
        chunk_ids_b = sorted(r.chunk_id for r in conn.execute(select(_table(meta_b, "chunks"))).all())
        graph_ids_b = sorted(r.graph_id for r in conn.execute(select(_table(meta_b, "graphs"))).all())
    assert chunk_ids_a == chunk_ids_b
    assert graph_ids_a == graph_ids_b


def test_scenario_board_different_seed_differs(tmp_path: Path) -> None:
    store_a, store_b = tmp_path / "a", tmp_path / "b"
    store_a.mkdir()
    store_b.mkdir()
    url_a, _meta_a = _full_hub_store(store_a)
    url_b, _meta_b = _full_hub_store(store_b)
    a = _runner().invoke(cli, ["scenario", "board", "--url", url_a, "--chunks", "6", "--seed", "1"])
    b = _runner().invoke(cli, ["scenario", "board", "--url", url_b, "--chunks", "6", "--seed", "2"])
    assert a.exit_code == 0, a.output
    assert b.exit_code == 0, b.output
    a_ids = _scenario_chunk_statuses(a.output)
    b_ids = _scenario_chunk_statuses(b.output)
    assert [chunk_id for chunk_id, _status in a_ids] != [chunk_id for chunk_id, _status in b_ids]


def test_scenario_board_zero_chunks_is_refused(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "0"])
    assert result.exit_code != 0
    assert "--chunks" in result.output


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
