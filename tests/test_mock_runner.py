"""Unit + component coverage for the mock runner (``blizzard-mock:unit-test``).

The mock runner is a *driver*: it performs the runner's outbound protocol against a hub.
These tests wire it to an **in-process mock hub** (over ``httpx.ASGITransport`` — no
network) and drive it through the driver's own control API (a ``TestClient`` over the mock
runner app), asserting the happy path and **each of the nine runner-side levers** — the
misbehaviours a hub-under-test must reject or absorb. The mock hub does not itself
validate the route capability token (``stale_route_token``/``omit_route_token``, issue
#84b) — that check belongs to the real hub, exercised in ``blizzard``'s own
``tests/service/`` tier — so those two levers are asserted here at the driver level
(``held(...).last_submission``), proving what the mock runner *presents* rather than how
a hub reacts to it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app as create_hub_app
from blizzard_mock.mock_runner.app import create_app as create_runner_app
from blizzard_mock.mock_runner.domain.models import Held
from blizzard_mock.mock_runner.domain.service import MockRunnerService
from blizzard_mock.mock_runner.internal.httpx_gateway import HttpxHubGateway

_SPEC = {
    "entry": "build",
    "nodes": {
        "build": {
            "executor": "runner",
            "prompt": "b",
            "judgement_prompt": "j",
            "choices": [{"name": "pass", "description": "p", "to": "review"}],
        },
        "review": {
            "executor": "runner",
            "prompt": "r",
            "judgement_prompt": "j",
            "choices": [{"name": "pass", "description": "p", "to": "deliver"}],
        },
        "deliver": {"executor": "hub", "mode": "merge-to-main"},
    },
}


@pytest.fixture
def stack() -> tuple[TestClient, TestClient]:
    """A mock hub + a mock runner driving it in-process (no network)."""
    hub_app = create_hub_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
    hub_client = TestClient(hub_app)
    # The driver's gateway talks to the hub app over the same in-process sync TestClient
    # (a real ``httpx.Client``) — no sockets, no network.
    gateway = HttpxHubGateway(hub_client)
    runner_app = create_runner_app(gateway=gateway, clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
    return hub_client, TestClient(runner_app)


def _seed(hub: TestClient) -> str:
    return hub.post("/_seed/chunk", json=_SPEC).json()["chunk_id"]


def _claim(runner: TestClient, chunk_id: str) -> dict:
    return runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()


def _held(runner: TestClient, chunk_id: str) -> Held | None:
    """The driver's own held-lease record — introspection the ``TestClient`` surface
    doesn't expose, for asserting exactly what a route-token lever made it submit."""
    service: MockRunnerService = runner.app.state.service  # type: ignore[attr-defined]
    return service.held(chunk_id)


# --- happy path -------------------------------------------------------------


def test_driver_claims_and_completes_over_the_wire(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    assert runner.post("/_drive/register").json()["status"] == 201
    assert runner.post("/_drive/peek").json()["response"]["entries"][0]["chunk_id"] == chunk_id

    claim = _claim(runner, chunk_id)
    assert claim["claimed"] is True and claim["from_node_id"] == "build" and claim["epoch"] == 1
    # the driver reported lease.minted, so the hub's fence advanced.
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["latest_epoch"] == 1

    step1 = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert step1["response"]["outcome"] == "next"  # build -> review, accepted over the wire
    step2 = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert step2["response"]["outcome"] == "hub_node_taken"  # review -> deliver hub node
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["status"] == "done"


def test_lever_catalog_lists_all_nine(stack: tuple[TestClient, TestClient]) -> None:
    _hub, runner = stack
    assert set(runner.get("/_levers").json()["catalog"]) == {
        "delay",
        "drop_ack",
        "conflicting_fact",
        "unreachable",
        "replay",
        "stale_epoch",
        "stale_route_token",
        "omit_route_token",
        "lease_via_events",
    }


# --- levers (the hub-under-test observes each misbehaviour over the wire) ----


def test_lever_stale_epoch_is_rejected_by_the_hub(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/stale_epoch", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["response"]["outcome"] == "failure"  # the hub fenced the zombie (D-007)
    assert "stale" in out["response"]["detail"]
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == "build"  # no advance


def test_lever_conflicting_fact_is_rejected_by_the_hub(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/conflicting_fact", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["response"]["outcome"] == "failure"  # from_node did not match the hub's current node
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == "build"


def test_lever_replay_is_applied_once_by_the_hub(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/replay", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["response"]["outcome"] == "next"  # first delivery advances
    assert out["replayed"]["response"]["outcome"] == "next"  # the duplicate is idempotent (D-090)
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == "review"  # advanced exactly once


def test_lever_unreachable_leaves_a_claimed_but_unfinished_chunk(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/unreachable", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["drove"] is False and "unreachable" in out["reason"]  # vanished mid-lease
    detail = hub.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert detail["status"] == "running" and detail["current_node_id"] == "build"  # still claimed, unfinished


def test_lever_drop_ack_does_not_advance_the_driver_but_the_hub_applied(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/drop_ack", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["dropped_ack"] is True and out["status"] == 200
    # the hub applied the transition; the driver just did not advance its held lease.
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == "review"


def test_default_completion_presents_the_claims_own_route_token(stack: tuple[TestClient, TestClient]) -> None:
    """Present-valid is the unlevered default (issue #84b): stamped from the claim
    response, mirroring the real runner's stash-and-stamp — no lever needed."""
    hub, runner = stack
    chunk_id = _seed(hub)
    claimed = _claim(runner, chunk_id)
    real_token = claimed["response"]["route_token"]

    runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"})

    held = _held(runner, chunk_id)
    assert held is not None
    submitted = held.last_submission
    assert submitted["route_token"] == real_token


def test_lever_stale_route_token_submits_a_wrong_token(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    claimed = _claim(runner, chunk_id)
    real_token = claimed["response"]["route_token"]
    runner.post("/_levers/stale_route_token", json={"chunk_id": chunk_id})

    runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"})

    held = _held(runner, chunk_id)
    assert held is not None
    submitted = held.last_submission
    assert submitted["route_token"] and submitted["route_token"] != real_token


def test_lever_omit_route_token_submits_no_token(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/omit_route_token", json={"chunk_id": chunk_id})

    runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"})

    held = _held(runner, chunk_id)
    assert held is not None
    submitted = held.last_submission
    assert "route_token" not in submitted


def test_default_lease_report_hits_the_dedicated_leases_route(stack: tuple[TestClient, TestClient]) -> None:
    """The default transport (no lever) is the dedicated ``/leases`` route, not the
    batched ``/events`` push — the hub's fence still advances (unchanged behavior)."""
    hub, runner = stack
    chunk_id = _seed(hub)
    hub.post("/_captured/reset")
    claim = _claim(runner, chunk_id)
    assert claim["claimed"] is True and claim["epoch"] == 1
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["latest_epoch"] == 1
    requests = hub.get("/_captured").json()["requests"]
    paths = [r["path"] for r in requests]
    assert f"/api/fleet/chunks/{chunk_id}/leases" in paths
    assert "/api/fleet/events" not in paths


def test_lever_lease_via_events_routes_the_report_through_events(stack: tuple[TestClient, TestClient]) -> None:
    """``lease_via_events`` retains the mock's original transport: the fence-advancing
    report rides the batched ``/events`` push instead of the dedicated route."""
    hub, runner = stack
    chunk_id = _seed(hub)
    runner.post("/_levers/lease_via_events", json={"chunk_id": chunk_id})
    hub.post("/_captured/reset")
    claim = _claim(runner, chunk_id)
    assert claim["claimed"] is True and claim["epoch"] == 1
    # the report still lands: the hub's fence advances exactly as the default path does.
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["latest_epoch"] == 1
    requests = hub.get("/_captured").json()["requests"]
    paths = [r["path"] for r in requests]
    assert f"/api/fleet/chunks/{chunk_id}/leases" not in paths
    assert "/api/fleet/events" in paths


# --- new drive verbs ---------------------------------------------------------


def test_drive_escalate_records_the_escalation_over_the_wire(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    out = runner.post(
        "/_drive/escalate", json={"chunk_id": chunk_id, "takeover_command": "git checkout -b rescue"}
    ).json()
    assert out["drove"] is True and out["status"] == 202
    detail = hub.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert detail["escalation"] == {
        "epoch": 1,
        "takeover_command": "git checkout -b rescue",
        "wrapped_takeover_command": "",
    }


def test_drive_escalate_forwards_the_wrapped_takeover_command_to_the_hub(
    stack: tuple[TestClient, TestClient],
) -> None:
    """``/_drive/escalate`` forwards ``wrapped_takeover_command`` through
    ``MockRunnerService.escalate`` to the hub gateway (issue #251) — a positional
    forward at ``drive_escalate`` that drops the field would leave this empty even
    though the request body carried it."""
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    out = runner.post(
        "/_drive/escalate",
        json={
            "chunk_id": chunk_id,
            "takeover_command": "git checkout -b rescue",
            "wrapped_takeover_command": f"blizzard runner takeover {chunk_id} --dir /runner",
        },
    ).json()
    assert out["drove"] is True and out["status"] == 202
    detail = hub.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert detail["escalation"] == {
        "epoch": 1,
        "takeover_command": "git checkout -b rescue",
        "wrapped_takeover_command": f"blizzard runner takeover {chunk_id} --dir /runner",
    }


def test_drive_decide_parks_the_chunk_at_the_gate(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    out = runner.post("/_drive/decide", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["drove"] is True
    assert out["response"]["outcome"] == "parked_at_gate"
    assert hub.get(f"/api/fleet/chunks/{chunk_id}").json()["status"] == "needs_human"


def test_drive_ask_mints_a_question_and_poll_answer_reads_it_unanswered(
    stack: tuple[TestClient, TestClient],
) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    asked = runner.post(
        "/_drive/ask", json={"chunk_id": chunk_id, "question": "which db?", "options": ["sqlite", "postgres"]}
    ).json()
    assert asked["drove"] is True
    question_id = asked["question_id"]

    polled = runner.post("/_drive/poll-answer", json={"question_id": question_id}).json()
    assert polled["status"] == 200
    assert polled["response"]["question_id"] == question_id
    assert polled["response"]["question"] == "which db?"
    assert polled["response"]["answered"] is False

    # the question is also visible off the chunk detail (hub-side minted state).
    detail = hub.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert any(q["question_id"] == question_id for q in detail["questions"])


def test_drive_push_transcript_applies_over_the_transcript_lanes_own_route(
    stack: tuple[TestClient, TestClient],
) -> None:
    """review round 8 F7: no service test previously drove a transcript push from a mock
    runner — ``IHubGateway`` stopped at ``push_facts``. ``push_transcripts`` closes that
    gap, proving the transcript lane's own route (``/transcripts``, distinct from
    ``/events``) is reachable end to end through the mock runner's driver, not just via a
    raw client straight at the mock hub."""
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)

    pushed = runner.post("/_drive/push-transcript", json={"chunk_id": chunk_id, "segment_id": "sg_1"}).json()

    assert pushed["drove"] is True
    assert pushed["status"] == 200
    assert pushed["response"]["applied"] == [1]
    assert pushed["response"]["capped"] == []


def test_drive_push_transcript_requires_a_held_chunk(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)

    pushed = runner.post("/_drive/push-transcript", json={"chunk_id": chunk_id}).json()

    assert pushed["drove"] is False


def test_drive_poll_answer_reflects_an_operator_answer(stack: tuple[TestClient, TestClient]) -> None:
    """Drives the answered path through the hub's test-control answer route
    (``POST /_seed/answer``, added alongside this parity work by the hub side)."""
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    asked = runner.post("/_drive/ask", json={"chunk_id": chunk_id, "question": "which db?"}).json()
    question_id = asked["question_id"]

    answer_resp = hub.post("/_seed/answer", json={"question_id": question_id, "answer": "postgres"})
    assert answer_resp.status_code == 200

    polled = runner.post("/_drive/poll-answer", json={"question_id": question_id}).json()
    assert polled["response"]["answered"] is True
    assert polled["response"]["answer"] == "postgres"


def test_drive_pause_sets_the_runners_local_pause_brake(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    runner.post("/_drive/register")
    out = runner.post("/_drive/pause", json={"by": "operator", "reason": "investigating"}).json()
    assert out["status"] == 200
    view = hub.get("/api/fleet/runners/runner-mock").json()
    assert view["locally_paused"] is True
    assert view["locally_paused_by"] == "operator"
    assert view["locally_paused_reason"] == "investigating"


def test_drive_resume_clears_the_runners_local_pause_brake(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    runner.post("/_drive/register")
    runner.post("/_drive/pause", json={"by": "operator"})
    out = runner.post("/_drive/resume", json={"by": "operator"}).json()
    assert out["status"] == 200
    view = hub.get("/api/fleet/runners/runner-mock").json()
    assert view["locally_paused"] is False


def test_lever_delay_slows_a_drive_call(stack: tuple[TestClient, TestClient]) -> None:
    import time

    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/delay", json={"chunk_id": chunk_id, "payload": {"ms": 200}})
    started = time.monotonic()
    runner.post("/_drive/get-chunk", json={"chunk_id": chunk_id})
    assert time.monotonic() - started >= 0.18


# --------------------------------------------------------------------------------- #
# Git-commit declaration channel (issue #143, Phase 3) — the served route + its
# drive-plane lever, both landing in the same local store, no hub call.
# --------------------------------------------------------------------------------- #


def test_served_route_records_a_git_commit_declaration(stack: tuple[TestClient, TestClient]) -> None:
    _hub, runner = stack
    resp = runner.post(
        "/api/leases/lease_1/git-commits",
        json={"forge": "github", "repo": "blizzard", "branch": "feat/x", "commit": "abc123"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"recorded": True, "lease_id": "lease_1", "repo": "blizzard"}

    read_back = runner.post("/_drive/get-git-commits", json={"lease_id": "lease_1"}).json()
    assert read_back["declarations"] == {
        "blizzard": {"forge": "github", "repo": "blizzard", "branch": "feat/x", "commit": "abc123"}
    }


def test_drive_declare_git_commit_lands_in_the_same_store_as_the_served_route(
    stack: tuple[TestClient, TestClient],
) -> None:
    _hub, runner = stack
    driven = runner.post(
        "/_drive/declare-git-commit",
        json={
            "lease_id": "lease_2",
            "forge": "github",
            "repo": "blizzard-mock",
            "branch": "main",
            "commit": "cafef00d",
        },
    ).json()
    assert driven == {"recorded": True, "lease_id": "lease_2", "repo": "blizzard-mock"}

    read_back = runner.post("/_drive/get-git-commits", json={"lease_id": "lease_2"}).json()
    assert read_back["declarations"] == {
        "blizzard-mock": {"forge": "github", "repo": "blizzard-mock", "branch": "main", "commit": "cafef00d"}
    }


def test_re_declaring_the_same_repo_overwrites_the_prior_declaration(stack: tuple[TestClient, TestClient]) -> None:
    _hub, runner = stack
    runner.post(
        "/_drive/declare-git-commit",
        json={"lease_id": "lease_3", "forge": "github", "repo": "blizzard", "branch": "feat/x", "commit": "first"},
    )
    runner.post(
        "/_drive/declare-git-commit",
        json={"lease_id": "lease_3", "forge": "github", "repo": "blizzard", "branch": "feat/x", "commit": "second"},
    )
    read_back = runner.post("/_drive/get-git-commits", json={"lease_id": "lease_3"}).json()
    assert read_back["declarations"]["blizzard"]["commit"] == "second"


def test_drive_reset_clears_declared_git_commits(stack: tuple[TestClient, TestClient]) -> None:
    _hub, runner = stack
    runner.post(
        "/_drive/declare-git-commit",
        json={"lease_id": "lease_4", "forge": "github", "repo": "blizzard", "branch": "feat/x", "commit": "abc123"},
    )
    runner.post("/_drive/reset")
    read_back = runner.post("/_drive/get-git-commits", json={"lease_id": "lease_4"}).json()
    assert read_back["declarations"] == {}
