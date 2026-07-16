"""Unit + component coverage for the mock hub (``blizzard-mock:unit-test``).

Drives the hub-mirror surface over a ``TestClient`` (in-process, no network): the happy
path — seed → peek → claim → fence → complete → a hub node derives ``done`` — plus **each
of the six levers**, asserting the named edge state a runner-under-test would then have to
survive. No ``blizzard`` import: the mock stands alone.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app

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
    "pm_pointers": [{"source": "o-r", "ref": "1"}],
}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC))))


def _seed(client: TestClient) -> str:
    resp = client.post("/_seed/chunk", json=_SPEC)
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _claim_and_fence(client: TestClient, chunk_id: str, *, epoch: int = 1) -> None:
    """Claim then report the lease.minted fence, mirroring a real runner's FILL+PULL."""
    assert client.post("/api/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 201
    ack = client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": epoch, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": epoch}}],
        },
    )
    assert ack.status_code == 200 and epoch in ack.json()["applied"]


# --- happy path -------------------------------------------------------------


def test_happy_path_ingest_to_done(client: TestClient) -> None:
    chunk_id = _seed(client)
    assert [e["chunk_id"] for e in client.get("/api/queue/peek").json()["entries"]] == [chunk_id]

    claim = client.post("/api/routes", json={"chunk_id": chunk_id, "runner_id": "r1", "environment_ids": ["e1"]})
    assert claim.status_code == 201
    assert claim.json()["envelope"]["node"]["node_name"] == "build"
    # claimed chunks leave the ready queue.
    assert client.get("/api/queue/peek").json()["entries"] == []

    client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    step1 = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert step1.json()["outcome"] == "next"
    assert step1.json()["next_envelope"]["node"]["node_name"] == "review"

    client.post(
        "/api/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 2, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 2}}],
        },
    )
    step2 = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 2, "runner_id": "r1", "from_node_id": "review"},
    )
    assert step2.json()["outcome"] == "hub_node_taken"  # the deliver hub node took over
    assert client.get(f"/api/chunks/{chunk_id}").json()["status"] == "done"


def test_second_claim_conflicts(client: TestClient) -> None:
    chunk_id = _seed(client)
    assert client.post("/api/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 201
    conflict = client.post("/api/routes", json={"chunk_id": chunk_id, "runner_id": "r2"})
    assert conflict.status_code == 409
    assert conflict.json()["held_by_runner_id"] == "r1"


def test_registry_register_and_pause_readback(client: TestClient) -> None:
    reg = client.post("/api/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    assert reg.status_code == 201 and reg.json()["first_registration"] is True
    assert (
        client.post("/api/runners", json={"runner_id": "r1", "workspace_id": "ws"}).json()["first_registration"]
        is False
    )
    assert client.get("/api/runners/r1").json()["paused"] is False


# --- levers -----------------------------------------------------------------


def test_lever_catalog_lists_all_six(client: TestClient) -> None:
    catalog = client.get("/_levers").json()["catalog"]
    assert set(catalog) == {"delay", "drop_ack", "conflicting_fact", "unreachable", "replay", "stale_envelope"}


def test_lever_unreachable_heals_mid_lease(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    assert client.post("/_levers/unreachable", json={"remaining": 2}).status_code == 200
    assert client.get(f"/api/chunks/{chunk_id}/envelope").status_code == 503
    assert client.get(f"/api/chunks/{chunk_id}/envelope").status_code == 503
    assert client.get(f"/api/chunks/{chunk_id}/envelope").status_code == 200  # healed after 2


def test_lever_stale_envelope_fences_out_the_completion(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post("/_levers/stale_envelope", json={"chunk_id": chunk_id})
    env = client.get(f"/api/chunks/{chunk_id}/envelope").json()
    assert env["epoch"] == 0  # stale: latest_epoch (1) - 1
    rejected = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": env["epoch"], "runner_id": "r1", "from_node_id": "build"},
    )
    assert rejected.json()["outcome"] == "failure"
    assert "stale" in rejected.json()["detail"]


def test_lever_drop_ack_advances_but_502s_and_reapply_is_idempotent(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post("/_levers/drop_ack", json={"chunk_id": chunk_id, "remaining": 1})
    dropped = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert dropped.status_code == 503  # the ack is dropped
    assert client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == "review"  # but the write landed
    reflush = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert reflush.status_code == 200 and reflush.json()["outcome"] == "next"  # idempotent re-apply (D-090)
    assert client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == "review"  # no double advance


def test_lever_replay_re_emits_the_previous_response(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    first = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert first.json()["outcome"] == "next"
    node_after_first = client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"]
    client.post("/_levers/replay", json={"chunk_id": chunk_id})
    replayed = client.post(
        f"/api/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "review"},
    )
    assert replayed.json()["outcome"] == "next"  # the previous response, duplicated
    assert client.get(f"/api/chunks/{chunk_id}").json()["current_node_id"] == node_after_first  # no advance


def test_lever_conflicting_fact_reports_a_foreign_holder(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    # armed single-shot (remaining=1): the conflicting fact surfaces once, then self-expires.
    client.post(
        "/_levers/conflicting_fact",
        json={"chunk_id": chunk_id, "remaining": 1, "payload": {"runner_id": "ghost-runner"}},
    )
    assert client.get(f"/api/chunks/{chunk_id}").json()["route"]["runner_id"] == "ghost-runner"
    assert client.get(f"/api/chunks/{chunk_id}").json()["route"]["runner_id"] == "r1"


def test_lever_delay_slows_the_response(client: TestClient) -> None:
    import time

    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post("/_levers/delay", json={"chunk_id": chunk_id, "payload": {"ms": 200}})
    started = time.monotonic()
    client.get(f"/api/chunks/{chunk_id}")
    assert time.monotonic() - started >= 0.18
