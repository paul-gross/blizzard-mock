"""Unit + component coverage for the mock runner (``blizzard-mock:unit-test``).

The mock runner is a *driver*: it performs the runner's outbound protocol against a hub.
These tests wire it to an **in-process mock hub** (over ``httpx.ASGITransport`` — no
network) and drive it through the driver's own control API (a ``TestClient`` over the mock
runner app), asserting the happy path and **each of the six runner-side levers** — the
misbehaviours a hub-under-test must reject or absorb.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app as create_hub_app
from blizzard_mock.mock_runner.app import create_app as create_runner_app
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


# --- happy path -------------------------------------------------------------


def test_driver_claims_and_completes_over_the_wire(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    assert runner.post("/_drive/register").json()["status"] == 201
    assert runner.post("/_drive/peek").json()["response"]["entries"][0]["chunk_id"] == chunk_id

    claim = _claim(runner, chunk_id)
    assert claim["claimed"] is True and claim["from_node_id"] == "build" and claim["epoch"] == 1
    # the driver reported lease.minted, so the hub's fence advanced.
    assert hub.get(f"/api/chunks/{chunk_id}").json()["latest_epoch"] == 1

    step1 = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert step1["response"]["outcome"] == "next"  # build -> review, accepted over the wire
    step2 = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert step2["response"]["outcome"] == "hub_node_taken"  # review -> deliver hub node
    assert hub.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_lever_catalog_lists_all_six(stack: tuple[TestClient, TestClient]) -> None:
    _hub, runner = stack
    assert set(runner.get("/_levers").json()["catalog"]) == {
        "delay",
        "drop_ack",
        "conflicting_fact",
        "unreachable",
        "replay",
        "stale_epoch",
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
    assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == "build"  # no advance


def test_lever_conflicting_fact_is_rejected_by_the_hub(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/conflicting_fact", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["response"]["outcome"] == "failure"  # from_node did not match the hub's current node
    assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == "build"


def test_lever_replay_is_applied_once_by_the_hub(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/replay", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["response"]["outcome"] == "next"  # first delivery advances
    assert out["replayed"]["response"]["outcome"] == "next"  # the duplicate is idempotent (D-090)
    assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == "review"  # advanced exactly once


def test_lever_unreachable_leaves_a_claimed_but_unfinished_chunk(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/unreachable", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["drove"] is False and "unreachable" in out["reason"]  # vanished mid-lease
    detail = hub.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running" and detail["current_node_id"] == "build"  # still claimed, unfinished


def test_lever_drop_ack_does_not_advance_the_driver_but_the_hub_applied(stack: tuple[TestClient, TestClient]) -> None:
    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/drop_ack", json={"chunk_id": chunk_id})
    out = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
    assert out["dropped_ack"] is True and out["status"] == 200
    # the hub applied the transition; the driver just did not advance its held lease.
    assert hub.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == "review"


def test_lever_delay_slows_a_drive_call(stack: tuple[TestClient, TestClient]) -> None:
    import time

    hub, runner = stack
    chunk_id = _seed(hub)
    _claim(runner, chunk_id)
    runner.post("/_levers/delay", json={"chunk_id": chunk_id, "payload": {"ms": 200}})
    started = time.monotonic()
    runner.post("/_drive/get-chunk", json={"chunk_id": chunk_id})
    assert time.monotonic() - started >= 0.18
