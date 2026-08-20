"""Unit coverage for the per-status chunk fact composer (``blizzard-mock:unit-test``).

Pure, no store: ``compose_chunk`` is a plain function over already-loaded data
(``bzh:domain-takes-objects``), so every one of the nine derivable statuses is
exercised directly against the exact ``FactRow`` table set
``blizzard.hub.domain.work.derive_chunk_status`` reads to arrive there,
first-match-wins — proving the composed set matches the precedence and never
includes a fact that would rank higher (e.g. ``--status running`` never emits a
terminal transition or an escalation).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import cast

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_data.domain.facts import FactRow
from blizzard_mock.mock_data.domain.hub.chunk_seed import ChunkCompositionError, ChunkSeed, compose_chunk
from blizzard_mock.mock_data.domain.hub.graph_seed import GraphCompositionError, GraphContext, NodeRef

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _graph() -> GraphContext:
    return GraphContext(
        graph_id="gr_test",
        entry_node_id="nd_build",
        nodes={
            "build": NodeRef(node_id="nd_build", executor="runner"),
            "deliver": NodeRef(node_id="nd_deliver", executor="hub"),
        },
    )


def _tables(rows: list[FactRow]) -> set[str]:
    return {row.table for row in rows}


_DEFAULT_WORKSPACE_ID = "workspace-seed"  # mirrors chunk_seed.py's own default


def _compose(
    status: str,
    *,
    chunk_id: str | None = None,
    node_name: str | None = None,
    work_refs: Sequence[tuple[str, str]] = (),
    runner_id: str = "runner-seed",
    epoch: int = 1,
    workspace_id: str = _DEFAULT_WORKSPACE_ID,
    mirrored: bool = False,
) -> ChunkSeed:
    return compose_chunk(
        status=status,
        graph=_graph(),
        clock=FixedClock(_NOW),
        rng=random.Random(1),
        chunk_id=chunk_id,
        node_name=node_name,
        work_refs=work_refs,
        runner_id=runner_id,
        epoch=epoch,
        workspace_id=workspace_id,
        mirrored=mirrored,
    )


# --- exact per-status fact sets, precedence-ordered -------------------------


def test_stopped_lands_only_chunk_stopped_beyond_the_base_row() -> None:
    seed = _compose("stopped")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "chunk_stopped"}


def test_done_lands_a_two_hop_terminal_path() -> None:
    """``done`` travels its full two-hop path — ``build``->``deliver``, then
    ``deliver``->the reserved terminal — sharing one epoch, so the chunk carries
    two selectable node-steps rather than one."""
    seed = _compose("done")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "lease_facts", "transitions"}
    transitions = sorted(
        (row for row in seed.rows if row.table == "transitions"),
        key=lambda r: cast(datetime, r.values["recorded_at"]),
    )
    assert len(transitions) == 2
    hop1, hop2 = transitions
    assert (hop1.values["from_node_id"], hop1.values["to_node_id"]) == ("nd_build", "nd_deliver")
    assert hop1.values["choice_name"] == "approved"
    assert (hop2.values["from_node_id"], hop2.values["to_node_id"]) == ("nd_deliver", "done")
    assert hop2.values["choice_name"] == "landed"
    assert hop1.values["epoch"] == hop2.values["epoch"]


def test_done_landing_from_build_lands_one_hop_not_a_self_edge() -> None:
    """``--node build`` names the terminal hop's own origin, so there is no
    predecessor hop left to compose — one transition, not a ``build``->``build``
    self-edge keying the same node-step twice."""
    seed = _compose("done", node_name="build")
    transitions = [row for row in seed.rows if row.table == "transitions"]
    assert len(transitions) == 1
    assert (transitions[0].values["from_node_id"], transitions[0].values["to_node_id"]) == ("nd_build", "done")


def test_needs_human_lands_an_escalation_and_no_route_or_terminal() -> None:
    seed = _compose("needs_human")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "transitions", "escalations"}
    transition = next(row for row in seed.rows if row.table == "transitions")
    assert transition.values["to_node_id"] != "done"


def test_needs_human_composes_a_wrapped_takeover_command_placeholder() -> None:
    """``needs_human`` seeds a non-empty ``wrapped_takeover_command`` placeholder by
    default (issue #251) — see blizzard's ``chunk-escalation.ts`` for the rendering rule
    this exercises."""
    seed = _compose("needs_human", chunk_id="ch_fixed")
    escalation = next(row for row in seed.rows if row.table == "escalations")
    assert escalation.values["wrapped_takeover_command"] == "blizzard runner takeover ch_fixed --dir <runner-dir>"


def test_waiting_on_human_lands_an_open_question() -> None:
    seed = _compose("waiting_on_human")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "transitions", "questions"}
    assert not any(row.table == "escalations" for row in seed.rows)


def test_paused_lands_a_pause_fact_reading_paused() -> None:
    seed = _compose("paused")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "transitions", "chunk_pause_facts"}
    pause = next(row for row in seed.rows if row.table == "chunk_pause_facts")
    assert pause.values["paused"] is True


def test_delivering_lands_a_transition_into_the_hub_node() -> None:
    seed = _compose("delivering")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "lease_facts", "route_created", "transitions"}
    transition = next(row for row in seed.rows if row.table == "transitions")
    assert transition.values["to_node_id"] == "nd_deliver"
    assert not any(row.table in ("chunk_stopped", "escalations", "questions", "chunk_pause_facts") for row in seed.rows)


def test_running_lands_a_live_route_and_a_non_hub_transition() -> None:
    seed = _compose("running")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted", "lease_facts", "route_created", "transitions"}
    transition = next(row for row in seed.rows if row.table == "transitions")
    assert transition.values["to_node_id"] == "nd_build"
    assert not any(row.table == "route_released" for row in seed.rows)
    assert not any(row.table in ("chunk_stopped", "escalations", "questions", "chunk_pause_facts") for row in seed.rows)


def test_running_route_carries_a_caller_supplied_workspace_id() -> None:
    """Regression: ``route_created.workspace_id`` used to hardcode the module's own
    default regardless of caller intent — a caller attributing the route to a
    runner registered under a specific workspace (e.g. ``scenario_seed.py``) needs
    the route to claim that same workspace, not a fixed placeholder."""
    seed = _compose("running", workspace_id="workspace-07")
    route = next(row for row in seed.rows if row.table == "route_created")
    assert route.values["workspace_id"] == "workspace-07"


def test_not_ready_lands_only_the_base_chunk_row() -> None:
    seed = _compose("not_ready")
    assert _tables(seed.rows) == {"chunks"}


def test_ready_lands_promoted_and_nothing_that_outranks_it() -> None:
    seed = _compose("ready")
    assert _tables(seed.rows) == {"chunks", "chunk_promoted"}


# --- work refs ---------------------------------------------------------------


def test_work_refs_land_chunk_work_ref_rows() -> None:
    seed = _compose("ready", work_refs=[("gh", "1"), ("gh", "2")])
    refs = [row for row in seed.rows if row.table == "chunk_work_refs"]
    assert [(r.values["source"], r.values["ref"]) for r in refs] == [("gh", "1"), ("gh", "2")]


# --- invalid combinations refuse rather than silently misderive -------------


def test_delivering_refuses_a_non_hub_node() -> None:
    with pytest.raises(ChunkCompositionError, match="hub-executor"):
        _compose("delivering", node_name="build")


def test_running_refuses_a_hub_node() -> None:
    with pytest.raises(ChunkCompositionError, match="hub-executed"):
        _compose("running", node_name="deliver")


def test_ready_refuses_a_node_name() -> None:
    with pytest.raises(ChunkCompositionError, match="mints no transition"):
        _compose("ready", node_name="build")


def test_not_ready_refuses_a_node_name() -> None:
    with pytest.raises(ChunkCompositionError, match="mints no transition"):
        _compose("not_ready", node_name="build")


def test_unknown_status_is_refused() -> None:
    with pytest.raises(ChunkCompositionError, match="unknown status"):
        _compose("bogus-status")


def test_unknown_node_name_is_refused() -> None:
    with pytest.raises(GraphCompositionError, match="nope"):
        _compose("needs_human", node_name="nope")


# --- reproducibility ----------------------------------------------------------


def test_compose_chunk_is_reproducible_with_the_same_seed() -> None:
    graph = _graph()
    a = compose_chunk(status="running", graph=graph, clock=FixedClock(_NOW), rng=random.Random(42))
    b = compose_chunk(status="running", graph=graph, clock=FixedClock(_NOW), rng=random.Random(42))
    assert a.chunk_id == b.chunk_id
    assert a.rows == b.rows


def test_compose_chunk_differs_with_a_different_seed() -> None:
    graph = _graph()
    a = compose_chunk(status="running", graph=graph, clock=FixedClock(_NOW), rng=random.Random(1))
    b = compose_chunk(status="running", graph=graph, clock=FixedClock(_NOW), rng=random.Random(2))
    assert a.chunk_id != b.chunk_id


# --- explicit chunk_id / epoch / runner_id override --------------------------


def test_explicit_chunk_id_and_epoch_and_runner_id_are_honored() -> None:
    seed = _compose("needs_human", chunk_id="ch_fixed", epoch=7, runner_id="r-9")
    assert seed.chunk_id == "ch_fixed"
    escalation = next(row for row in seed.rows if row.table == "escalations")
    assert escalation.values["chunk_id"] == "ch_fixed"
    assert escalation.values["epoch"] == 7
    chunk_row = next(row for row in seed.rows if row.table == "chunks")
    assert chunk_row.values["chunk_id"] == "ch_fixed"


# --- mirrored (``scenario fleet``'s pinned mode) -----------------------------


def test_mirrored_lands_a_route_created_for_a_status_that_mints_none_of_its_own() -> None:
    seed = _compose("needs_human", mirrored=True)
    routes = [row for row in seed.rows if row.table == "route_created"]
    assert len(routes) == 1
    assert routes[0].values["runner_id"] == "runner-seed"


def test_mirrored_is_a_no_op_for_a_status_that_already_mints_one() -> None:
    """``running``/``delivering`` already land a live route — mirroring adds no second one."""
    seed = _compose("running", mirrored=True)
    routes = [row for row in seed.rows if row.table == "route_created"]
    assert len(routes) == 1


def test_unmirrored_lands_no_route_for_a_status_that_mints_none_of_its_own() -> None:
    seed = _compose("needs_human", mirrored=False)
    assert not any(row.table == "route_created" for row in seed.rows)
