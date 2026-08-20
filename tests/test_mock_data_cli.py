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

import pytest
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


def _runner_store(tmp_path: Path) -> tuple[str, MetaData, Table, Table]:
    """A sqlite store mirroring the runner's own ``leases`` + ``lease_context``
    (``blizzard/src/blizzard/runner/store/schema.py``). Unlike the hub, the runner
    schema declares no foreign keys at all — ``leases.chunk_id`` is a plain column —
    so this fixture proves ``reset`` still clears a store shaped that way."""
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    engine = create_engine(url)
    meta = MetaData()
    leases = Table(
        "leases",
        meta,
        Column("lease_id", String, primary_key=True),
        Column("chunk_id", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("runner_id", String, nullable=False),
        Column("pid", Integer, nullable=True),
        Column("process_start_time", String, nullable=True),
        Column("session_id", String, nullable=True),
        Column("created_at", DateTime, nullable=False),
    )
    lease_context = Table(
        "lease_context",
        meta,
        Column("lease_id", String, primary_key=True),
        Column("chunk_id", String, nullable=False),
        Column("graph_id", String, nullable=False),
        Column("node_id", String, nullable=False),
        Column("node_name", String, nullable=False),
        Column("retries_max", Integer, nullable=False),
        Column("session_name", String, nullable=True),
        Column("resolved_model", String, nullable=True),
        Column("resolved_effort", String, nullable=True),
        Column("resolved_compaction_window", String, nullable=True),
        Column("recorded_at", DateTime, nullable=False),
    )
    meta.create_all(engine)
    return url, meta, leases, lease_context


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
        Column("wrapped_takeover_command", Text, nullable=False, server_default=""),
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
        "artifacts",
        meta,
        Column("artifact_id", String, primary_key=True),
        Column("chunk_id", String, ForeignKey("chunks.chunk_id"), nullable=False),
        Column("node_id", String, nullable=False),
        Column("node_name", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("name", String, nullable=False),
        Column("kind", String, nullable=False),
        Column("data", Text, nullable=False),
        Column("repo", String, nullable=True),
        Column("forge", String, nullable=True),
        Column("produced_at", DateTime, nullable=False),
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


def _full_runner_store(tmp_path: Path) -> tuple[str, MetaData]:
    """A sqlite store mirroring the runner's own ``leases``/``lease_context``/
    ``usage_facts``/``transcript_segments`` (``blizzard/src/blizzard/runner/store/
    schema.py``) — the subset ``create lease``/``create usage``/
    ``create transcript-segment --store runner`` write into. No foreign keys at all,
    unlike the hub: ``leases.chunk_id`` is a plain column."""
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    engine = create_engine(url)
    meta = MetaData()
    Table(
        "leases",
        meta,
        Column("lease_id", String, primary_key=True),
        Column("chunk_id", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("runner_id", String, nullable=False),
        Column("pid", Integer, nullable=True),
        Column("process_start_time", String, nullable=True),
        Column("session_id", String, nullable=True),
        Column("created_at", DateTime, nullable=False),
    )
    Table(
        "lease_context",
        meta,
        Column("lease_id", String, primary_key=True),
        Column("chunk_id", String, nullable=False),
        Column("graph_id", String, nullable=False),
        Column("node_id", String, nullable=False),
        Column("node_name", String, nullable=False),
        Column("retries_max", Integer, nullable=False),
        Column("session_name", String, nullable=True),
        Column("resolved_model", String, nullable=True),
        Column("resolved_effort", String, nullable=True),
        Column("resolved_compaction_window", String, nullable=True),
        Column("recorded_at", DateTime, nullable=False),
    )
    Table(
        "usage_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("lease_id", String, nullable=False),
        Column("chunk_id", String, nullable=False),
        Column("node_id", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("generation", Integer, nullable=False),
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
        "transcript_segments",
        meta,
        Column("segment_id", String, primary_key=True),
        Column("chunk_id", String, nullable=False),
        Column("node_id", String, nullable=False),
        Column("epoch", Integer, nullable=False),
        Column("generation", Integer, nullable=False),
        Column("lease_id", String, nullable=False),
        Column("session_id", String, nullable=False),
        Column("cursor", String, nullable=True),
        Column("shipped_bytes", Integer, nullable=False),
        Column("shipped_turns", Integer, nullable=False),
        Column("normalizer_version", String, nullable=False),
        Column("harness_version", String, nullable=True),
        Column("truncated_reason", String, nullable=True),
        Column("truncated_reason_severity", Integer, nullable=True),
        Column("shipping_stopped_reason", String, nullable=True),
        Column("sidechain_warned_agents", Text, nullable=True),
        Column("agent_tool_use_ids", Text, nullable=True),
        Column("truncated_reasons_warned", Text, nullable=True),
        Column("supersedes", String, nullable=True),
        Column("finalized_at", DateTime, nullable=True),
        Column("stamped_at", DateTime, nullable=False),
    )
    Table(
        "env_bindings",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("chunk_id", String, nullable=False),
        Column("environment_id", String, nullable=False),
        Column("workdir", String, nullable=False),
        Column("bound_at", DateTime, nullable=False),
    )
    Table(
        "asks",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("lease_id", String, nullable=False),
        Column("chunk_id", String, nullable=False),
        Column("question_id", String, nullable=False),
        Column("question", Text, nullable=False),
        Column("options", Text, nullable=False),
        Column("session_id", String, nullable=True),
        Column("asked_at", DateTime, nullable=False),
    )
    Table(
        "park_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("lease_id", String, nullable=False),
        Column("chunk_id", String, nullable=False),
        Column("question_id", String, nullable=False),
        Column("parked_at", DateTime, nullable=False),
    )
    Table(
        "lease_closures",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("lease_id", String, nullable=False),
        Column("chunk_id", String, nullable=False),
        Column("node_id", String, nullable=False),
        Column("reason", String, nullable=False),
        Column("closed_at", DateTime, nullable=False),
    )
    Table(
        "takeovers",
        meta,
        Column("takeover_id", String, primary_key=True),
        Column("chunk_id", String, nullable=False),
        Column("lease_id", String, nullable=True),
        Column("session_id", String, nullable=True),
        Column("workdir", String, nullable=False),
        Column("fence_epoch", Integer, nullable=True),
        Column("opened_at", DateTime, nullable=False),
    )
    Table(
        "outbound_buffer",
        meta,
        Column("seq", Integer, primary_key=True, autoincrement=True),
        Column("kind", String, nullable=False),
        Column("chunk_id", String, nullable=True),
        Column("lease_id", String, nullable=True),
        Column("payload", Text, nullable=False),
        Column("created_at", DateTime, nullable=False),
        Column("acked_at", DateTime, nullable=True),
    )
    Table(
        "local_pause_facts",
        meta,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("runner_id", String, nullable=False),
        Column("paused", Boolean, nullable=False),
        Column("set_at", DateTime, nullable=False),
        Column("set_by", String, nullable=False),
    )
    meta.create_all(engine)
    return url, meta


def _lease_select_rows(meta: MetaData, engine, chunk_id: str) -> list:  # type: ignore[no-untyped-def]
    """Read a lease back the way the daemon does — ``_lease_select()``'s own inner
    join of ``leases`` to ``lease_context`` (``sqlalchemy_store.py`` lines 1789-1808)
    — never a bare ``leases`` row, which the drift guard alone cannot catch."""
    leases = _table(meta, "leases")
    lease_context = _table(meta, "lease_context")
    stmt = (
        select(leases, lease_context.c.graph_id, lease_context.c.node_id, lease_context.c.node_name)
        .join(lease_context, lease_context.c.lease_id == leases.c.lease_id)
        .where(leases.c.chunk_id == chunk_id)
    )
    with engine.begin() as conn:
        return conn.execute(stmt).all()


def _table(meta: MetaData, name: str) -> Table:
    return meta.tables[name]


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SEEDED_CLOCK_ANCHOR = datetime(2024, 1, 1, tzinfo=UTC)  # what --seed pins the clock to


def _decode_minted_at(minted_id: str) -> datetime:
    """The instant a prefixed ULID spells, decoded the way the real wire decodes it —
    mirrored independently here (no ``blizzard`` import), like every other mirror in
    this repo. The real reader accepts only ``<prefix>_<26-char Crockford ULID>`` and
    reads the instant out of the leading 48 bits; a row whose id is hand-written
    rather than minted renders a null timestamp instead of failing, so the shape is
    asserted here rather than trusted.
    """
    _, sep, ulid = minted_id.partition("_")
    assert sep == "_", f"{minted_id!r} carries no prefix separator"
    assert len(ulid) == 26, f"{minted_id!r} has a {len(ulid)}-char tail, not the 26 a ULID has"
    millis = 0
    for char in ulid[:10]:
        index = _CROCKFORD.find(char.upper())
        assert index >= 0, f"{minted_id!r} carries {char!r}, outside the Crockford alphabet"
        millis = millis * 32 + index
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


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
        ["create", "artifact", "--help"],
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
    for subcommand in (
        "runner",
        "graph",
        "chunk",
        "artifact",
        "usage",
        "lease",
        "escalation",
        "question",
        "event",
        "runner-pause",
    ):
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


def test_reset_clears_all_rows_in_a_runner_shaped_store(tmp_path: Path) -> None:
    """``reset`` is schema-agnostic (it never imports ``blizzard``), so it must clear
    a runner-shaped store too — one with no foreign keys at all, unlike the hub's."""
    url, _meta, leases, lease_context = _runner_store(tmp_path)
    engine = create_engine(url)
    now = datetime.now(UTC)
    with engine.begin() as conn:
        conn.execute(
            insert(leases).values(lease_id="lease_1", chunk_id="chunk_1", epoch=1, runner_id="runner-1", created_at=now)
        )
        conn.execute(
            insert(lease_context).values(
                lease_id="lease_1",
                chunk_id="chunk_1",
                graph_id="graph_1",
                node_id="node_1",
                node_name="build",
                retries_max=3,
                recorded_at=now,
            )
        )

    result = _runner().invoke(cli, ["reset", "--store", "runner", "--url", url])
    assert result.exit_code == 0, result.output
    assert "cleared 2 row(s)" in result.output
    with engine.begin() as conn:
        assert conn.execute(select(leases)).all() == []
        assert conn.execute(select(lease_context)).all() == []


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
    """The ``done`` arm travels its two-hop path — the terminal-landing transition
    (not the chunk's only one) is what makes it done."""
    url, meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "done"])
    assert result.exit_code == 0, result.output
    chunk_id = result.output.strip()
    with create_engine(url).begin() as conn:
        transitions = _table(meta, "transitions")
        rows = conn.execute(
            select(transitions.c.to_node_id)
            .where(transitions.c.chunk_id == chunk_id)
            .order_by(transitions.c.recorded_at)
        ).all()
    assert [r.to_node_id for r in rows][-1] == "done"
    assert len(rows) == 2


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


# --- create artifact (implemented) --------------------------------------------


def test_create_artifact_git_commit_lands_the_pinned_ref(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "pr-branch",
            "--kind",
            "git_commit",
            "--node",
            "build",
            "--repo",
            "acme/widget",
            "--branch",
            "feature/x",
            "--commit",
            "abc123",
        ],
    )
    assert result.exit_code == 0, result.output
    artifact_id = result.output.strip()
    assert artifact_id.startswith("art_")

    with create_engine(url).begin() as conn:
        row = conn.execute(
            select(_table(meta, "artifacts")).where(_table(meta, "artifacts").c.artifact_id == artifact_id)
        ).one()
    assert row.chunk_id == chunk_id
    assert row.node_name == "build"
    assert row.kind == "git_commit"
    assert row.data == "feature/x:abc123"
    assert row.repo == "acme/widget"
    assert row.forge is None


def test_create_artifact_asset_lands_verbatim_content(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "findings: looks good",
        ],
    )
    assert result.exit_code == 0, result.output
    artifact_id = result.output.strip()
    with create_engine(url).begin() as conn:
        row = conn.execute(
            select(_table(meta, "artifacts")).where(_table(meta, "artifacts").c.artifact_id == artifact_id)
        ).one()
    assert row.kind == "asset"
    assert row.data == "findings: looks good"
    assert row.repo is None
    assert row.forge is None


def test_create_artifact_asset_generates_content_of_the_requested_size(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "big-log",
            "--kind",
            "asset",
            "--content-size",
            "128",
        ],
    )
    assert result.exit_code == 0, result.output
    artifact_id = result.output.strip()
    with create_engine(url).begin() as conn:
        data = conn.execute(
            select(_table(meta, "artifacts").c.data).where(_table(meta, "artifacts").c.artifact_id == artifact_id)
        ).scalar_one()
    assert len(data) == 128


def test_create_artifact_defaults_node_and_epoch_from_the_chunks_transition_and_lease(tmp_path: Path) -> None:
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
            "r-artifact",
            "--epoch",
            "4",
        ],
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "hello",
        ],
    )
    assert result.exit_code == 0, result.output
    artifact_id = result.output.strip()
    with create_engine(url).begin() as conn:
        row = conn.execute(
            select(_table(meta, "artifacts")).where(_table(meta, "artifacts").c.artifact_id == artifact_id)
        ).one()
    assert row.node_name == "build"
    assert row.epoch == 4


def test_create_artifact_refuses_an_inconsistent_kind_payload_pair(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--branch",
            "feature/x",
        ],
    )
    assert result.exit_code != 0
    assert "git_commit-only" in result.output


def test_create_artifact_refuses_a_chunk_that_was_never_seeded(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            "ch_ghost",
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "hello",
        ],
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_create_artifact_refuses_a_chunk_with_no_transition_to_default_a_node_from(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "hello",
        ],
    )
    assert result.exit_code != 0
    assert "has no transitions" in result.output


def test_create_artifact_refuses_a_chunk_that_was_never_seeded_given_an_explicit_node(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            "ch_ghost",
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "hello",
            "--node",
            "build",
        ],
    )
    assert result.exit_code != 0
    assert "does not exist" in result.output


def test_create_artifact_refuses_a_runner_store(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "hello",
        ],
    )
    assert result.exit_code != 0
    assert "lives in the hub store" in result.output


def test_create_artifact_several_calls_land_under_different_node_epoch_pairs(tmp_path: Path) -> None:
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running"]
    ).output.strip()

    for node_name, epoch in (("build", 1), ("deliver", 2), ("build", 3)):
        result = runner.invoke(
            cli,
            [
                "create",
                "artifact",
                "--store",
                "hub",
                "--url",
                url,
                "--chunk",
                chunk_id,
                "--name",
                "review-notes",
                "--kind",
                "asset",
                "--content",
                f"content for epoch {epoch}",
                "--node",
                node_name,
                "--epoch",
                str(epoch),
            ],
        )
        assert result.exit_code == 0, result.output

    with create_engine(url).begin() as conn:
        rows = conn.execute(
            select(_table(meta, "artifacts")).where(_table(meta, "artifacts").c.chunk_id == chunk_id)
        ).all()
    assert sorted((r.node_name, r.epoch) for r in rows) == [("build", 1), ("build", 3), ("deliver", 2)]


def test_create_artifact_mints_an_id_the_wires_decode_accepts(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "running", "--seed", "5"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "review-notes",
            "--kind",
            "asset",
            "--content",
            "hello",
            "--seed",
            "5",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _decode_minted_at(result.output.strip()) == _SEEDED_CLOCK_ANCHOR


def test_create_artifact_defaults_a_done_chunks_node_to_the_one_it_landed_from(tmp_path: Path) -> None:
    """A ``done`` chunk's newest transition lands on the reserved terminal marker, which
    is no graph node — the default falls back to the node that transition left, the same
    node ``create chunk --status done --node`` names."""
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "done"]
    ).output.strip()

    result = runner.invoke(
        cli,
        [
            "create",
            "artifact",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            chunk_id,
            "--name",
            "pr-branch",
            "--kind",
            "git_commit",
            "--repo",
            "acme/widget",
            "--branch",
            "feature/x",
            "--commit",
            "abc123",
        ],
    )
    assert result.exit_code == 0, result.output

    with create_engine(url).begin() as conn:
        row = conn.execute(select(_table(meta, "artifacts"))).one()
        node_id = conn.execute(
            select(_table(meta, "graph_nodes").c.node_id).where(_table(meta, "graph_nodes").c.name == "deliver")
        ).scalar_one()
    assert row.node_name == "deliver"
    assert row.node_id == node_id


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


def test_create_usage_defaults_a_done_chunks_node_to_the_one_it_landed_from(tmp_path: Path) -> None:
    """Same regression as ``create artifact``'s: a ``done`` chunk's newest transition
    lands on the reserved terminal marker, which is no graph node — the default falls
    back to the node that transition left, rather than attributing to the raw marker
    id (``_resolve_node_id_from_newest_transition`` is the one owner both verbs read
    through)."""
    url, meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "done"]
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
        node_id = conn.execute(
            select(_table(meta, "graph_nodes").c.node_id).where(_table(meta, "graph_nodes").c.name == "deliver")
        ).scalar_one()
    assert row.node_id == node_id


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


# --- create lease --store runner (implemented) -------------------------------


def test_create_lease_runner_store_is_readable_back_through_the_daemons_own_join(tmp_path: Path) -> None:
    """The read-back that matters: a lease composed by ``create lease --store runner``
    must satisfy the same inner join the daemon's own ``_lease_select()`` runs —
    a row in ``leases`` alone is not enough, since the drift guard checks columns,
    never joins."""
    url, meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "lease",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_hub_1",
            "--runner-id",
            "r-1",
            "--epoch",
            "3",
            "--node",
            "deliver",
            "--graph-id",
            "gr_1",
            "--retries-max",
            "4",
        ],
    )
    assert result.exit_code == 0, result.output

    joined = _lease_select_rows(meta, create_engine(url), "ch_hub_1")
    assert len(joined) == 1, "the mint must satisfy the daemon's own inner join, not just land a bare `leases` row"
    row = joined[0]
    assert row.epoch == 3
    assert row.runner_id == "r-1"
    assert row.graph_id == "gr_1"
    assert row.node_id == "deliver"
    assert row.node_name == "deliver"


def test_create_lease_runner_store_seed_pins_created_at_too(tmp_path: Path) -> None:
    """Regression: ``--seed`` pinned id-minting but stamped ``created_at`` off a
    real ``SystemClock()`` regardless — two runs at the same seed minted equal ids
    but unequal timestamps, contradicting the flag's own "byte-identical runs"
    promise."""
    url, meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "lease",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--runner-id",
            "r-1",
            "--seed",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "leases"))).all()
    assert len(rows) == 1
    assert rows[0].created_at.replace(tzinfo=UTC) == _SEEDED_CLOCK_ANCHOR


def test_create_lease_runner_store_accepts_a_chunk_id_no_hub_store_holds(tmp_path: Path) -> None:
    """The runner schema declares no foreign keys — ``leases.chunk_id`` is a plain
    column — so a lease naming a chunk id no hub knows about is accepted rather than
    refused (the daemon abandoning the lease as a consequence is documented, not
    enforced, here)."""
    url, _meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        ["create", "lease", "--store", "runner", "--url", url, "--chunk", "ch_nowhere", "--runner-id", "r-1"],
    )
    assert result.exit_code == 0, result.output


def test_create_lease_runner_store_refuses_hub_only_output_unchanged(tmp_path: Path) -> None:
    """The hub path's printed output is exactly what it was before this store gained
    a runner branch."""
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()
    result = runner.invoke(
        cli,
        ["create", "lease", "--store", "hub", "--url", url, "--chunk", chunk_id, "--runner-id", "r-1", "--epoch", "2"],
    )
    assert result.exit_code == 0, result.output
    assert result.output.strip() == f"created lease for chunk {chunk_id!r} (epoch=2, runner_id='r-1')"


@pytest.mark.parametrize(
    ("flag", "value", "expected"),
    [
        ("--node", "build", "has no column on the hub's lease_facts"),
        ("--graph-id", "g-1", "has no column on the hub's lease_facts"),
        ("--retries-max", "9", "has no column on the hub's lease_facts"),
        ("--seed", "42", "--seed mints nothing on the hub's lease_facts"),
    ],
)
def test_create_lease_refuses_runner_only_flags_against_the_hub_store(
    tmp_path: Path, flag: str, value: str, expected: str
) -> None:
    url, _meta = _full_hub_store(tmp_path)
    runner = _runner()
    chunk_id = runner.invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url, "--status", "ready"]
    ).output.strip()
    result = runner.invoke(
        cli,
        ["create", "lease", "--store", "hub", "--url", url, "--chunk", chunk_id, "--runner-id", "r-1", flag, value],
    )
    assert result.exit_code != 0
    assert expected in result.output
    assert "runner store only" in result.output


# --- create usage --store runner (implemented) --------------------------------


def test_create_usage_runner_store_lands_a_row_keyed_by_lease_id(tmp_path: Path) -> None:
    url, meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--node",
            "build",
            "--epoch",
            "1",
            "--lease-id",
            "lease_1",
            "--kind",
            "spawn",
            "--model",
            "claude-x",
            "--input-tokens",
            "100",
            "--no-cost",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "usage_facts"))).all()
    assert [(r.lease_id, r.generation, r.cost_usd) for r in rows] == [("lease_1", 1, None)]


def test_create_usage_runner_store_refuses_runner_id(tmp_path: Path) -> None:
    url, _meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--node",
            "build",
            "--epoch",
            "1",
            "--lease-id",
            "lease_1",
            "--runner-id",
            "r-1",
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
    assert "has no column on the runner store's usage_facts" in result.output


def test_create_usage_refuses_runner_only_flags_against_the_hub_store(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
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
            "spawn",
            "--model",
            "claude-x",
            "--input-tokens",
            "1",
            "--no-cost",
            "--lease-id",
            "lease_1",
        ],
    )
    assert result.exit_code != 0
    assert "has no column on the hub's usage_facts" in result.output


# --- create transcript-segment (implemented, runner-only) ---------------------


def test_create_transcript_segment_lands_a_row_and_prints_its_id(tmp_path: Path) -> None:
    url, meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "transcript-segment",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--node",
            "build",
            "--lease-id",
            "lease_1",
            "--session-id",
            "sess_1",
        ],
    )
    assert result.exit_code == 0, result.output
    segment_id = result.output.strip()
    assert segment_id.startswith("seg_")
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "transcript_segments"))).all()
    assert [r.segment_id for r in rows] == [segment_id]
    assert rows[0].finalized_at is None


def test_create_transcript_segment_seed_pins_stamped_at_too(tmp_path: Path) -> None:
    """Same regression as ``create lease --store runner``'s: ``--seed`` pinned
    id-minting but stamped ``stamped_at``/``finalized_at`` off a real
    ``SystemClock()`` regardless."""
    url, meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "transcript-segment",
            "--store",
            "runner",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--node",
            "build",
            "--lease-id",
            "lease_1",
            "--session-id",
            "sess_1",
            "--finalized",
            "--seed",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        rows = conn.execute(select(_table(meta, "transcript_segments"))).all()
    assert len(rows) == 1
    assert rows[0].stamped_at.replace(tzinfo=UTC) == _SEEDED_CLOCK_ANCHOR
    assert rows[0].finalized_at.replace(tzinfo=UTC) == _SEEDED_CLOCK_ANCHOR


def test_create_transcript_segment_refuses_a_hub_store(tmp_path: Path) -> None:
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "transcript-segment",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            "ch_1",
            "--node",
            "build",
            "--lease-id",
            "lease_1",
            "--session-id",
            "sess_1",
        ],
    )
    assert result.exit_code != 0
    assert "lives in the runner store" in result.output


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
        takeover, wrapped = conn.execute(
            select(
                _table(meta, "escalations").c.takeover_command,
                _table(meta, "escalations").c.wrapped_takeover_command,
            ).where(_table(meta, "escalations").c.chunk_id == chunk_id)
        ).one()
    assert takeover == f"cd <workdir> && <resume {chunk_id}>"
    # The bare default path a human seeding a board takes writes the synthesized
    # wrapped placeholder too — pinned through cli.py -> service.seed() -> the row,
    # not just at the composer.
    assert wrapped == f"blizzard runner takeover {chunk_id} --dir <runner-dir>"


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


def test_create_escalation_explicit_wrapped_takeover_command_lands_the_given_value(tmp_path: Path) -> None:
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
            "--wrapped-takeover-command",
            "blizzard runner takeover ch_x --dir /custom",
        ],
    )
    assert result.exit_code == 0, result.output
    with create_engine(url).begin() as conn:
        wrapped = conn.execute(
            select(_table(meta, "escalations").c.wrapped_takeover_command).where(
                _table(meta, "escalations").c.chunk_id == chunk_id
            )
        ).scalar()
    assert wrapped == "blizzard runner takeover ch_x --dir /custom"


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
    runner = _runner()
    # `runner_pause_facts.runner_id` carries a real FK to `runner_registrations` (unlike
    # `runner_local_pause_facts`, which has none) — register the runner first.
    registered = runner.invoke(cli, ["create", "runner", "--store", "hub", "--url", url, "--runner-id", "r-1"])
    assert registered.exit_code == 0, registered.output
    result = runner.invoke(
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


# --- scenario fleet -----------------------------------------------------------


def test_scenario_fleet_seeds_both_stores(tmp_path: Path) -> None:
    hub_url, hub_meta = _full_hub_store(tmp_path)
    runner_url, runner_meta = _full_runner_store(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "scenario",
            "fleet",
            "--hub-url",
            hub_url,
            "--runner-url",
            runner_url,
            "--runner-id",
            "runner-pin",
            "--seed",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    assert f"seeded the hub half into: {hub_url}" in result.output
    assert f"seeded the runner half into: {runner_url}" in result.output
    assert "runner: 'runner-pin'" in result.output

    with create_engine(hub_url).begin() as conn:
        chunk_rows = conn.execute(select(_table(hub_meta, "chunks"))).all()
        route_rows = conn.execute(select(_table(hub_meta, "route_created"))).all()
    assert len(chunk_rows) == 6
    # The mirrored chunks plus the `running` chunk's own route — never the `ready`
    # one, which a live route would derive `running`.
    assert len(route_rows) == 3
    assert all(row.runner_id == "runner-pin" for row in route_rows)

    with create_engine(runner_url).begin() as conn:
        lease_rows = conn.execute(select(_table(runner_meta, "leases"))).all()
        pause_rows = conn.execute(select(_table(runner_meta, "local_pause_facts"))).all()
    assert len(lease_rows) == 2
    assert all(row.runner_id == "runner-pin" for row in lease_rows)
    assert len(pause_rows) == 1
    assert pause_rows[0].paused


def test_scenario_fleet_requires_a_hub_target(tmp_path: Path) -> None:
    _hub_url, _hub_meta = _full_hub_store(tmp_path)
    runner_url, _runner_meta = _full_runner_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "fleet", "--runner-url", runner_url, "--runner-id", "runner-pin"])
    assert result.exit_code != 0
    assert "--hub-url" in result.output
    assert "--hub-dir" in result.output


def test_scenario_fleet_requires_a_runner_target(tmp_path: Path) -> None:
    hub_url, _hub_meta = _full_hub_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "fleet", "--hub-url", hub_url])
    assert result.exit_code != 0
    assert "--runner-url" in result.output
    assert "--runner-dir" in result.output


def test_scenario_fleet_requires_runner_id_without_runner_dir(tmp_path: Path) -> None:
    hub_url, _hub_meta = _full_hub_store(tmp_path)
    runner_url, _runner_meta = _full_runner_store(tmp_path)
    result = _runner().invoke(cli, ["scenario", "fleet", "--hub-url", hub_url, "--runner-url", runner_url])
    assert result.exit_code != 0
    assert "--runner-id" in result.output


def test_scenario_fleet_reads_the_pinned_runner_id_from_runner_dir(tmp_path: Path) -> None:
    hub_url, _hub_meta = _full_hub_store(tmp_path)
    runner_url, runner_meta = _full_runner_store(tmp_path)
    runtime_dir = tmp_path / "runner-runtime"
    runtime_dir.mkdir()
    (runtime_dir / "blizzard-runner.toml").write_text(f'db_url = "{runner_url}"\nrunner_id = "runner-local"\n')

    result = _runner().invoke(cli, ["scenario", "fleet", "--hub-url", hub_url, "--runner-dir", str(runtime_dir)])
    assert result.exit_code == 0, result.output
    assert "runner: 'runner-local'" in result.output
    with create_engine(runner_url).begin() as conn:
        lease_rows = conn.execute(select(_table(runner_meta, "leases"))).all()
    assert all(row.runner_id == "runner-local" for row in lease_rows)


def test_scenario_fleet_refuses_runner_id_redundant_with_runner_dir(tmp_path: Path) -> None:
    hub_url, _hub_meta = _full_hub_store(tmp_path)
    runner_url, _runner_meta = _full_runner_store(tmp_path)
    runtime_dir = tmp_path / "runner-runtime"
    runtime_dir.mkdir()
    (runtime_dir / "blizzard-runner.toml").write_text(f'db_url = "{runner_url}"\nrunner_id = "runner-local"\n')

    result = _runner().invoke(
        cli,
        [
            "scenario",
            "fleet",
            "--hub-url",
            hub_url,
            "--runner-dir",
            str(runtime_dir),
            "--runner-id",
            "runner-other",
        ],
    )
    assert result.exit_code != 0
    assert "redundant" in result.output


def test_scenario_fleet_mirrors_the_same_chunk_ids_under_one_runner(tmp_path: Path) -> None:
    hub_url, hub_meta = _full_hub_store(tmp_path)
    runner_url, runner_meta = _full_runner_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "scenario",
            "fleet",
            "--hub-url",
            hub_url,
            "--runner-url",
            runner_url,
            "--runner-id",
            "runner-pin",
            "--seed",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output

    with create_engine(hub_url).begin() as conn:
        hub_chunk_ids = {row.chunk_id for row in conn.execute(select(_table(hub_meta, "chunks"))).all()}
    with create_engine(runner_url).begin() as conn:
        runner_chunk_ids = {row.chunk_id for row in conn.execute(select(_table(runner_meta, "leases"))).all()}
        binding_chunk_ids = {row.chunk_id for row in conn.execute(select(_table(runner_meta, "env_bindings"))).all()}
    assert runner_chunk_ids <= hub_chunk_ids
    assert binding_chunk_ids == runner_chunk_ids


def test_scenario_fleet_same_seed_is_byte_identical_across_two_fresh_stores(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    hub_url_a, hub_meta_a = _full_hub_store(tmp_path / "a")
    runner_url_a, runner_meta_a = _full_runner_store(tmp_path / "a")
    hub_url_b, hub_meta_b = _full_hub_store(tmp_path / "b")
    runner_url_b, runner_meta_b = _full_runner_store(tmp_path / "b")

    for hub_url, runner_url in ((hub_url_a, runner_url_a), (hub_url_b, runner_url_b)):
        result = _runner().invoke(
            cli,
            [
                "scenario",
                "fleet",
                "--hub-url",
                hub_url,
                "--runner-url",
                runner_url,
                "--runner-id",
                "runner-pin",
                "--seed",
                "7",
            ],
        )
        assert result.exit_code == 0, result.output

    with create_engine(hub_url_a).begin() as conn:
        chunks_a = sorted(str(row.chunk_id) for row in conn.execute(select(_table(hub_meta_a, "chunks"))).all())
    with create_engine(hub_url_b).begin() as conn:
        chunks_b = sorted(str(row.chunk_id) for row in conn.execute(select(_table(hub_meta_b, "chunks"))).all())
    assert chunks_a == chunks_b

    with create_engine(runner_url_a).begin() as conn:
        leases_a = sorted(str(row.lease_id) for row in conn.execute(select(_table(runner_meta_a, "leases"))).all())
    with create_engine(runner_url_b).begin() as conn:
        leases_b = sorted(str(row.lease_id) for row in conn.execute(select(_table(runner_meta_b, "leases"))).all())
    assert leases_a == leases_b


def test_scenario_fleet_a_broken_runner_target_refuses_before_the_hub_half_lands(tmp_path: Path) -> None:
    """The runner half's own local-pause brake is written *first* (closing the window
    a live tick could otherwise land in before the mirror's brake stands) — so a
    runner-shaped store missing every table fails on that first write, before the hub
    half is ever composed or touched."""
    hub_url, hub_meta = _full_hub_store(tmp_path)
    # A runner-shaped store missing every table: the drift guard refuses it outright.
    broken_runner_url = f"sqlite:///{tmp_path / 'broken-runner.db'}"
    create_engine(broken_runner_url).connect().close()

    result = _runner().invoke(
        cli,
        [
            "scenario",
            "fleet",
            "--hub-url",
            hub_url,
            "--runner-url",
            broken_runner_url,
            "--runner-id",
            "runner-pin",
        ],
    )
    assert result.exit_code != 0
    assert "hub half not attempted" in result.output
    assert "hub half landed" not in result.output
    with create_engine(hub_url).begin() as conn:
        chunk_rows = conn.execute(select(_table(hub_meta, "chunks"))).all()
    assert len(chunk_rows) == 0


def test_scenario_fleet_a_hub_write_failure_after_the_brake_lands_names_the_brake_landed(tmp_path: Path) -> None:
    """Regression: the runner half's local-pause brake is written *first*, before the
    hub half is even attempted — so a hub write failure past that point leaves a real
    brake standing in the runner store. The old message (``"hub half not landed"``)
    said nothing about it, contradicting the docstring's "any write failure names
    which half, if any, had already landed" promise. Reproduced the way it was found:
    two ``--seed``-pinned runs against the same store pair, the second colliding on
    ``graphs.graph_id``."""
    hub_url, _hub_meta = _full_hub_store(tmp_path)
    runner_url, runner_meta = _full_runner_store(tmp_path)
    args = [
        "scenario",
        "fleet",
        "--hub-url",
        hub_url,
        "--runner-url",
        runner_url,
        "--runner-id",
        "runner-pin",
        "--seed",
        "3",
    ]

    first = _runner().invoke(cli, args)
    assert first.exit_code == 0, first.output

    second = _runner().invoke(cli, args)
    assert second.exit_code != 0
    assert "runner half's local pause landed" in second.output
    assert "hub half not landed" in second.output

    with create_engine(runner_url).begin() as conn:
        pause_rows = conn.execute(select(_table(runner_meta, "local_pause_facts"))).all()
    # The first run's brake, plus the second run's — the second run's brake write
    # succeeded even though its hub write then failed.
    assert len(pause_rows) == 2


def test_scenario_fleet_a_bad_runner_dsn_refuses_before_either_store_is_touched(tmp_path: Path) -> None:
    """A malformed DSN (``sqlalchemy.exc.ArgumentError``) is refused as a plain
    ``ClickException`` before either store is touched — never a raw traceback."""
    hub_url, hub_meta = _full_hub_store(tmp_path)

    result = _runner().invoke(
        cli,
        ["scenario", "fleet", "--hub-url", hub_url, "--runner-url", "not-a-dsn", "--runner-id", "runner-pin"],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "cannot open the runner store" in result.output
    with create_engine(hub_url).begin() as conn:
        chunk_rows = conn.execute(select(_table(hub_meta, "chunks"))).all()
    assert len(chunk_rows) == 0


def test_scenario_fleet_an_unreachable_runner_directory_refuses_before_either_store_is_touched(
    tmp_path: Path,
) -> None:
    """An unreachable target (``sqlalchemy.exc.OperationalError`` — e.g. a sqlite path
    whose parent directory doesn't exist) is refused the same clean way, before either
    store is touched."""
    hub_url, hub_meta = _full_hub_store(tmp_path)
    unreachable_runner_url = f"sqlite:///{tmp_path / 'nonexistent-dir' / 'runner.db'}"

    result = _runner().invoke(
        cli,
        [
            "scenario",
            "fleet",
            "--hub-url",
            hub_url,
            "--runner-url",
            unreachable_runner_url,
            "--runner-id",
            "runner-pin",
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "cannot open the runner store" in result.output
    with create_engine(hub_url).begin() as conn:
        chunk_rows = conn.execute(select(_table(hub_meta, "chunks"))).all()
    assert len(chunk_rows) == 0


def test_scenario_fleet_reuses_an_already_registered_runner(tmp_path: Path) -> None:
    """A pinned runner that's already registered (a live daemon re-registers itself
    every tick) is reused rather than colliding on ``runner_registrations``' PK — the
    same "reuse if present" precedent this composition root already applies to a
    named graph."""
    hub_url, hub_meta = _full_hub_store(tmp_path)
    runner_url, _runner_meta = _full_runner_store(tmp_path)
    with create_engine(hub_url).begin() as conn:
        conn.execute(
            insert(_table(hub_meta, "runner_registrations")),
            [
                {
                    "runner_id": "runner-pin",
                    "workspace_id": "ws-existing",
                    "registered_at": datetime(2023, 1, 1, tzinfo=UTC),
                    "last_seen_at": datetime(2023, 1, 1, tzinfo=UTC),
                }
            ],
        )

    result = _runner().invoke(
        cli,
        ["scenario", "fleet", "--hub-url", hub_url, "--runner-url", runner_url, "--runner-id", "runner-pin"],
    )
    assert result.exit_code == 0, result.output

    with create_engine(hub_url).begin() as conn:
        registration_rows = conn.execute(select(_table(hub_meta, "runner_registrations"))).all()
    assert len(registration_rows) == 1
    assert registration_rows[0].workspace_id == "ws-existing"


def test_scenario_fleet_a_too_small_chunks_leaves_both_stores_untouched(tmp_path: Path) -> None:
    hub_url, hub_meta = _full_hub_store(tmp_path)
    runner_url, runner_meta = _full_runner_store(tmp_path)

    result = _runner().invoke(
        cli,
        [
            "scenario",
            "fleet",
            "--hub-url",
            hub_url,
            "--runner-url",
            runner_url,
            "--runner-id",
            "runner-pin",
            "--chunks",
            "1",
        ],
    )
    assert result.exit_code != 0
    assert "--chunks" in result.output

    with create_engine(hub_url).begin() as conn:
        chunk_rows = conn.execute(select(_table(hub_meta, "chunks"))).all()
    assert len(chunk_rows) == 0
    with create_engine(runner_url).begin() as conn:
        pause_rows = conn.execute(select(_table(runner_meta, "local_pause_facts"))).all()
    assert len(pause_rows) == 0


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


def test_create_usage_against_a_store_missing_lease_facts_is_a_clean_click_exception(tmp_path: Path) -> None:
    """Regression: ``_resolve_usage_defaults``'s own lookup query used to run *before*
    the command's try block, so a ``SchemaDriftError`` it raised (a missing
    ``lease_facts`` table) escaped uncaught instead of surfacing as a clean
    ``ClickException`` like every other drift path in this tool."""
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    Table("chunks", meta, Column("chunk_id", String, primary_key=True))
    meta.create_all(engine)

    result = _runner().invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            "ch_missing",
            "--kind",
            "spawn",
            "--model",
            "m",
            "--input-tokens",
            "1",
            "--no-cost",
        ],
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "lease_facts" in result.output
    assert "Traceback" not in result.output


def test_create_event_against_a_store_missing_lease_facts_is_a_clean_click_exception(tmp_path: Path) -> None:
    """Same regression as above, for ``create event``'s ``_resolve_event_runner_id`` lookup."""
    url = f"sqlite:///{tmp_path / 'hub.db'}"
    engine = create_engine(url)
    meta = MetaData()
    Table("chunks", meta, Column("chunk_id", String, primary_key=True))
    meta.create_all(engine)

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
            "test.kind",
            "--severity",
            "info",
            "--message",
            "hi",
            "--chunk",
            "ch_missing",
        ],
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "lease_facts" in result.output
    assert "Traceback" not in result.output


# --- --seed reproducibility through the actual CLI (not just the pure composer) ---


def test_create_graph_same_seed_mints_the_same_id_across_two_stores(tmp_path: Path) -> None:
    """Regression: ``create graph`` kept a real ``SystemClock()`` regardless of
    ``--seed``, and ``ids.ulid`` draws its leading bits from the clock — so two runs
    at the same ``--seed`` minted *different* ids despite the documented
    reproducibility contract."""
    store_a, store_b = tmp_path / "a", tmp_path / "b"
    store_a.mkdir()
    store_b.mkdir()
    url_a, _meta_a = _full_hub_store(store_a)
    url_b, _meta_b = _full_hub_store(store_b)
    result_a = _runner().invoke(cli, ["create", "graph", "--store", "hub", "--url", url_a, "--seed", "1"])
    result_b = _runner().invoke(cli, ["create", "graph", "--store", "hub", "--url", url_b, "--seed", "1"])
    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output
    assert result_a.output.strip() == result_b.output.strip()


def test_create_chunk_same_seed_mints_the_same_id_across_two_stores(tmp_path: Path) -> None:
    """Same regression as above, for ``create chunk`` (and the graph it auto-mints)."""
    store_a, store_b = tmp_path / "a", tmp_path / "b"
    store_a.mkdir()
    store_b.mkdir()
    url_a, _meta_a = _full_hub_store(store_a)
    url_b, _meta_b = _full_hub_store(store_b)
    result_a = _runner().invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url_a, "--status", "ready", "--seed", "1"]
    )
    result_b = _runner().invoke(
        cli, ["create", "chunk", "--store", "hub", "--url", url_b, "--status", "ready", "--seed", "1"]
    )
    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output
    assert result_a.output.strip() == result_b.output.strip()


def test_create_question_same_seed_mints_the_same_id_across_two_stores(tmp_path: Path) -> None:
    """Same regression as above, for ``create question`` — a fixed ``--chunk-id``
    keeps the parked chunk itself identical across stores so only the question's
    own ``--seed`` reproducibility is under test."""
    store_a, store_b = tmp_path / "a", tmp_path / "b"
    store_a.mkdir()
    store_b.mkdir()
    url_a, _meta_a = _full_hub_store(store_a)
    url_b, _meta_b = _full_hub_store(store_b)
    for url in (url_a, url_b):
        chunk_result = _runner().invoke(
            cli,
            [
                "create",
                "chunk",
                "--store",
                "hub",
                "--url",
                url,
                "--status",
                "waiting_on_human",
                "--chunk-id",
                "ch-fixed",
            ],
        )
        assert chunk_result.exit_code == 0, chunk_result.output
    result_a = _runner().invoke(
        cli,
        ["create", "question", "--store", "hub", "--url", url_a, "--chunk", "ch-fixed", "--text", "q?", "--seed", "1"],
    )
    result_b = _runner().invoke(
        cli,
        ["create", "question", "--store", "hub", "--url", url_b, "--chunk", "ch-fixed", "--text", "q?", "--seed", "1"],
    )
    assert result_a.exit_code == 0, result_a.output
    assert result_b.exit_code == 0, result_b.output
    assert result_a.output.strip() == result_b.output.strip()


# --- referential integrity surfaces as a clean CLI error, not a silent orphan row ---


def test_create_runner_pause_fleet_refuses_an_unregistered_runner(tmp_path: Path) -> None:
    """Regression: no referential-integrity precondition meant ``--runner-id`` naming
    a runner that was never registered landed a silently-orphaned
    ``runner_pause_facts`` row (sqlite never enforced the FK by default).

    ``CliRunner.invoke`` captures an uncaught exception into ``result.exception``
    rather than writing it to ``result.output`` (a bare ``exit_code != 0`` plus
    ``"Traceback" not in result.output`` can never fail either way) — assert
    ``result.exception`` is the click-driven ``SystemExit``, not a raw
    ``IntegrityError``, the way the schema-drift regression tests above already do."""
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli, ["create", "runner-pause", "--store", "hub", "--url", url, "--runner-id", "ghost-runner", "--fleet"]
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "ghost-runner" in result.output or "constraint violation" in result.output


def test_create_usage_refuses_a_chunk_that_was_never_seeded(tmp_path: Path) -> None:
    """Same regression as above, for a ``--chunk`` naming a chunk that was never
    ``create chunk``-ed — see that test's docstring for why ``result.exception`` is
    the assertion that actually proves a clean ``ClickException``, not a bare exit
    code plus an output-string check."""
    url, _meta = _full_hub_store(tmp_path)
    result = _runner().invoke(
        cli,
        [
            "create",
            "usage",
            "--store",
            "hub",
            "--url",
            url,
            "--chunk",
            "ch_ghost",
            "--kind",
            "spawn",
            "--model",
            "m",
            "--input-tokens",
            "1",
            "--no-cost",
            "--node",
            "explicit-node",
        ],
    )
    assert result.exit_code != 0
    assert isinstance(result.exception, SystemExit)
    assert "constraint violation" in result.output


def test_create_runner_refuses_a_duplicate_runner_id(tmp_path: Path) -> None:
    """Regression: ``create_runner`` alone still caught only ``SchemaDriftError``,
    the pre-``e5074f7`` pattern every sibling verb was upgraded away from — so a
    duplicate ``--runner-id`` (a unique-constraint clash, not a schema drift) leaked
    a raw ``sqlalchemy.exc.IntegrityError`` traceback instead of the clean
    ``SeedIntegrityError`` message every other verb already gives that failure
    mode."""
    url, _meta = _full_hub_store(tmp_path)
    first = _runner().invoke(cli, ["create", "runner", "--store", "hub", "--url", url, "--runner-id", "dup-runner"])
    assert first.exit_code == 0, first.output
    second = _runner().invoke(cli, ["create", "runner", "--store", "hub", "--url", url, "--runner-id", "dup-runner"])
    assert second.exit_code != 0
    assert isinstance(second.exception, SystemExit)
    assert "constraint violation" in second.output


def test_scenario_board_rerun_without_reset_is_a_clean_click_exception(tmp_path: Path) -> None:
    """Regression: ``scenario board`` unconditionally mints a fresh graph under a
    fixed name, so re-running it against a store that already carries one (the
    easiest mistake to make with the tool's own flagship one-command entry point)
    used to raise a raw ``IntegrityError`` traceback, including local filesystem
    paths, instead of an actionable message."""
    url, _meta = _full_hub_store(tmp_path)
    first = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert first.exit_code == 0, first.output
    second = _runner().invoke(cli, ["scenario", "board", "--url", url, "--chunks", "6", "--seed", "1"])
    assert second.exit_code != 0
    assert isinstance(second.exception, SystemExit)
    assert "reset" in second.output.lower()


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
