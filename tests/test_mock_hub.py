"""Unit + component coverage for the mock hub (``blizzard-mock:unit-test``).

Drives the hub-mirror surface over a ``TestClient`` (in-process, no network): the happy
path — seed → peek → claim → fence → complete → a hub node derives ``done`` — plus **each
of the seven levers**, asserting the named edge state a runner-under-test would then have
to survive, and the full ``/events`` fact vocabulary (blizzard-mock#4). No ``blizzard``
import: the mock stands alone.
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
    "work_refs": [{"source": "o-r", "ref": "1"}],
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
    assert client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 201
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": epoch, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": epoch}}],
        },
    )
    assert ack.status_code == 200 and epoch in ack.json()["applied"]


# --- happy path -------------------------------------------------------------


def test_happy_path_ingest_to_done(client: TestClient) -> None:
    chunk_id = _seed(client)
    assert [e["chunk_id"] for e in client.get("/api/fleet/queue/peek").json()["entries"]] == [chunk_id]

    claim = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1", "environment_ids": ["e1"]})
    assert claim.status_code == 201
    assert claim.json()["envelope"]["node"]["node_name"] == "build"
    # claimed chunks leave the ready queue.
    assert client.get("/api/fleet/queue/peek").json()["entries"] == []

    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    step1 = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert step1.json()["outcome"] == "next"
    assert step1.json()["next_envelope"]["node"]["node_name"] == "review"

    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 2, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 2}}],
        },
    )
    step2 = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 2, "runner_id": "r1", "from_node_id": "review"},
    )
    assert step2.json()["outcome"] == "hub_node_taken"  # the deliver hub node took over
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["status"] == "done"


def test_second_claim_conflicts(client: TestClient) -> None:
    chunk_id = _seed(client)
    assert client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 201
    conflict = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r2"})
    assert conflict.status_code == 409
    assert conflict.json()["held_by_runner_id"] == "r1"


def test_rekey_route_token_returns_a_different_deterministic_token_than_the_claim(client: TestClient) -> None:
    chunk_id = _seed(client)
    claim = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})
    claimed_token = claim.json()["route_token"]

    rekey = client.post(f"/api/fleet/chunks/{chunk_id}/route-token")
    assert rekey.status_code == 200
    body = rekey.json()
    assert body["chunk_id"] == chunk_id
    assert body["route_token"] != claimed_token
    assert body["route_token"].startswith("mock-route-token-")

    # Deterministic, and a second rekey differs from the first.
    rekey_again = client.post(f"/api/fleet/chunks/{chunk_id}/route-token").json()
    assert rekey_again["route_token"] != body["route_token"]


def test_rekey_route_token_404s_on_an_unclaimed_chunk(client: TestClient) -> None:
    chunk_id = _seed(client)
    assert client.post(f"/api/fleet/chunks/{chunk_id}/route-token").status_code == 404


def test_registry_register_and_pause_readback(client: TestClient) -> None:
    reg = client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    assert reg.status_code == 201 and reg.json()["first_registration"] is True
    assert (
        client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"}).json()["first_registration"]
        is False
    )
    readback = client.get("/api/fleet/runners/r1").json()
    assert readback["hub_paused"] is False
    assert readback["locally_paused"] is False


def test_registration_accepts_optional_federation_identity(client: TestClient) -> None:
    """``url``/``redirect_uris`` (issue #95) — a wire-shape extension the mock hub round
    -trips, mirroring the real hub's own optional registration fields."""
    reg = client.post(
        "/api/fleet/runners",
        json={
            "runner_id": "r-fed",
            "workspace_id": "ws",
            "url": "https://r-fed.example",
            "redirect_uris": ["https://r-fed.example/api/auth/callback"],
        },
    )
    assert reg.status_code == 201, reg.text


# --- levers -----------------------------------------------------------------


def test_lever_catalog_lists_all_seven(client: TestClient) -> None:
    catalog = client.get("/_levers").json()["catalog"]
    assert set(catalog) == {
        "delay",
        "drop_ack",
        "conflicting_fact",
        "unreachable",
        "replay",
        "stale_envelope",
        "chunk_unknown",
    }


def test_lever_unreachable_heals_mid_lease(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    assert client.post("/_levers/unreachable", json={"remaining": 2}).status_code == 200
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 503
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 503
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 200  # healed after 2


def test_lever_stale_envelope_fences_out_the_completion(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post("/_levers/stale_envelope", json={"chunk_id": chunk_id})
    env = client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()
    assert env["epoch"] == 0  # stale: latest_epoch (1) - 1
    rejected = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": env["epoch"], "runner_id": "r1", "from_node_id": "build"},
    )
    assert rejected.json()["outcome"] == "failure"
    assert "stale" in rejected.json()["detail"]


# --- request capture (issue #86b) --------------------------------------------


def test_captured_records_the_authorization_header_on_an_api_call(client: TestClient) -> None:
    resp = client.post(
        "/api/fleet/runners",
        json={"runner_id": "r1", "workspace_id": "ws"},
        headers={"Authorization": "Bearer tok-123"},
    )
    assert resp.status_code == 201

    captured = client.get("/_captured").json()["requests"]
    assert len(captured) == 1
    entry = captured[0]
    assert entry["method"] == "POST"
    assert entry["path"] == "/api/fleet/runners"
    assert entry["headers"]["authorization"] == "Bearer tok-123"


def test_captured_omits_a_request_with_no_authorization_header(client: TestClient) -> None:
    client.get("/api/fleet/queue/peek")
    entry = client.get("/_captured").json()["requests"][0]
    assert "authorization" not in entry["headers"]


def test_captured_ignores_control_plane_calls(client: TestClient) -> None:
    _seed(client)  # POST /_seed/chunk
    client.get("/_levers")
    assert client.get("/_captured").json()["requests"] == []


def test_captured_reset_clears_the_log(client: TestClient) -> None:
    client.get("/api/fleet/queue/peek")
    assert len(client.get("/_captured").json()["requests"]) == 1
    assert client.post("/_captured/reset").status_code == 200
    assert client.get("/_captured").json()["requests"] == []


def test_captured_is_readable_even_while_unreachable_is_armed(client: TestClient) -> None:
    assert client.post("/_levers/unreachable", json={}).status_code == 200
    assert client.get("/api/fleet/queue/peek").status_code == 503
    # The control plane (incl. /_captured itself) stays exempt from `unreachable`, and the
    # 503'd call it recorded on the way in is still readable.
    captured = client.get("/_captured")
    assert captured.status_code == 200
    assert captured.json()["requests"][0]["path"] == "/api/fleet/queue/peek"


def test_lever_drop_ack_advances_but_502s_and_reapply_is_idempotent(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post("/_levers/drop_ack", json={"chunk_id": chunk_id, "remaining": 1})
    dropped = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert dropped.status_code == 503  # the ack is dropped
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == "review"  # but the write landed
    reflush = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert reflush.status_code == 200 and reflush.json()["outcome"] == "next"  # idempotent re-apply (D-090)
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == "review"  # no double advance


def test_lever_replay_re_emits_the_previous_response(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    first = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )
    assert first.json()["outcome"] == "next"
    node_after_first = client.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"]
    client.post("/_levers/replay", json={"chunk_id": chunk_id})
    replayed = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "review"},
    )
    assert replayed.json()["outcome"] == "next"  # the previous response, duplicated
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["current_node_id"] == node_after_first  # no advance


def test_lever_conflicting_fact_reports_a_foreign_holder(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    # armed single-shot (remaining=1): the conflicting fact surfaces once, then self-expires.
    client.post(
        "/_levers/conflicting_fact",
        json={"chunk_id": chunk_id, "remaining": 1, "payload": {"runner_id": "ghost-runner"}},
    )
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["route"]["runner_id"] == "ghost-runner"
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["route"]["runner_id"] == "r1"


def test_lever_delay_slows_the_response(client: TestClient) -> None:
    import time

    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post("/_levers/delay", json={"chunk_id": chunk_id, "payload": {"ms": 200}})
    started = time.monotonic()
    client.get(f"/api/fleet/chunks/{chunk_id}")
    assert time.monotonic() - started >= 0.18


def test_lever_chunk_unknown_404s_mid_lease_then_self_expires(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    assert client.post("/_levers/chunk_unknown", json={"chunk_id": chunk_id, "remaining": 2}).status_code == 200
    assert client.get(f"/api/fleet/chunks/{chunk_id}").status_code == 404
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 404
    # healed after 2 affected calls — the chunk's actual state was never deleted.
    healed = client.get(f"/api/fleet/chunks/{chunk_id}")
    assert healed.status_code == 200
    assert healed.json()["chunk_id"] == chunk_id


# --- new fleet routes (blizzard-mock#4) --------------------------------------


def test_get_question_returns_the_question_view(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 10,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "q1",
                        "chunk_id": chunk_id,
                        "epoch": 1,
                        "question": "merge now?",
                        "options": ["yes", "no"],
                        "asked_at": "2026-07-13T00:00:00+00:00",
                    },
                }
            ],
        },
    )
    view = client.get("/api/fleet/questions/q1")
    assert view.status_code == 200
    body = view.json()
    assert body["question_id"] == "q1"
    assert body["chunk_id"] == chunk_id
    assert body["runner_id"] == "r1"
    assert body["question"] == "merge now?"
    assert body["options"] == ["yes", "no"]
    assert body["answered"] is False
    # ... and it shows up on the chunk's own detail read too.
    detail_questions = client.get(f"/api/fleet/chunks/{chunk_id}").json()["questions"]
    assert [q["question_id"] for q in detail_questions] == ["q1"]


def test_get_question_404s_on_unknown_question(client: TestClient) -> None:
    assert client.get("/api/fleet/questions/unknown").status_code == 404


def test_report_lease_advances_the_fence_and_404s_on_unknown_chunk(client: TestClient) -> None:
    chunk_id = _seed(client)
    client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})
    resp = client.post(f"/api/fleet/chunks/{chunk_id}/leases", json={"epoch": 3, "runner_id": "r1"})
    assert resp.status_code == 202
    assert resp.json() == {"chunk_id": chunk_id}
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["latest_epoch"] == 3
    assert client.post("/api/fleet/chunks/unknown/leases", json={"epoch": 1, "runner_id": "r1"}).status_code == 404


def test_report_escalation_is_readable_on_chunk_detail_and_404s_on_unknown_chunk(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    resp = client.post(
        f"/api/fleet/chunks/{chunk_id}/escalations",
        json={"epoch": 1, "runner_id": "r1", "takeover_command": "blizzard runner takeover ch_1"},
    )
    assert resp.status_code == 202
    detail = client.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert detail["escalation"] == {"epoch": 1, "takeover_command": "blizzard runner takeover ch_1"}
    assert client.post("/api/fleet/chunks/unknown/escalations", json={"epoch": 1, "runner_id": "r1"}).status_code == 404


def test_hub_advance_completes_a_chunk_parked_at_the_entry_hub_node(client: TestClient) -> None:
    resp = client.post(
        "/_seed/chunk", json={"entry": "deliver", "nodes": {"deliver": {"executor": "hub", "mode": "merge-to-main"}}}
    )
    chunk_id = resp.json()["chunk_id"]
    client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["status"] == "running"

    advance = client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert advance.status_code == 200
    assert advance.json() == {
        "chunk_id": chunk_id,
        "status": "done",
        "ran": True,
        "outcome_choice": None,
        "to_node_name": None,
        "detail": "hub node advanced to done",
    }
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["status"] == "done"


def test_hub_advance_is_a_noop_when_not_parked_at_a_hub_node(client: TestClient) -> None:
    chunk_id = _seed(client)  # entry node "build" is executor: runner
    client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})
    advance = client.post(f"/api/fleet/chunks/{chunk_id}/hub-advance")
    assert advance.status_code == 200
    assert advance.json()["ran"] is False
    assert advance.json()["detail"] == "not parked at a hub command node"


def test_hub_advance_404s_on_unknown_chunk(client: TestClient) -> None:
    assert client.post("/api/fleet/chunks/unknown/hub-advance").status_code == 404


# --- /events full fact vocabulary + ack partitioning -------------------------


def test_events_escalation_recorded_sets_chunk_detail_escalation(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "escalation.recorded",
                    "payload": {"chunk_id": chunk_id, "epoch": 1, "takeover_command": "take it over"},
                }
            ],
        },
    )
    assert ack.json()["applied"] == [2]
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["escalation"] == {
        "epoch": 1,
        "takeover_command": "take it over",
    }


def test_events_answer_delivered_marks_the_question_answered(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "q1",
                        "chunk_id": chunk_id,
                        "epoch": 1,
                        "question": "?",
                        "asked_at": "2026-07-13T00:00:00+00:00",
                    },
                }
            ],
        },
    )
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 3, "kind": "answer.delivered", "payload": {"question_id": "q1", "chunk_id": chunk_id}}],
        },
    )
    assert ack.json()["applied"] == [3]
    polled = client.get("/api/fleet/questions/q1").json()
    assert polled["answered"] is True
    # The delivery is readable in its own right (blizzard#165), not folded into
    # `answered` — the real hub's view carries the same pair off `answer_deliveries`.
    assert polled["delivered"] is True
    assert polled["delivered_at"] is not None


def test_events_runner_locally_paused_and_resumed_are_runner_scoped(client: TestClient) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    paused = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "runner.locally_paused", "payload": {"by": "op", "reason": "maintenance"}}],
        },
    )
    assert paused.json()["applied"] == [1]
    view = client.get("/api/fleet/runners/r1").json()
    assert view["locally_paused"] is True
    assert view["locally_paused_by"] == "op"
    assert view["locally_paused_reason"] == "maintenance"

    resumed = client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": 2, "kind": "runner.locally_resumed", "payload": {}}]},
    )
    assert resumed.json()["applied"] == [2]
    view = client.get("/api/fleet/runners/r1").json()
    assert view["locally_paused"] is False
    assert view["locally_paused_by"] is None


def test_events_usage_recorded_is_accepted(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "usage.recorded",
                    "payload": {
                        "chunk_id": chunk_id,
                        "node_id": "build",
                        "epoch": 1,
                        "kind": "worker",
                        "model": "claude-opus-4-8",
                        "input_tokens": 10,
                        "output_tokens": 5,
                        "cache_read_tokens": 0,
                        "cache_create_tokens": 0,
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [2]
    assert ack.json()["rejected"] == []


def test_events_known_kind_on_an_unknown_chunk_is_still_applied(client: TestClient) -> None:
    """The batched ``_apply`` carries no chunk-existence check on the real hub (unlike
    the direct ``/leases``/``/escalations`` routes) — a known kind naming a chunk the
    mock doesn't hold still lands as applied and the mark advances; the mutation is
    just a no-op."""
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": "unknown", "epoch": 1}},
            ],
        },
    )
    body = ack.json()
    assert body["applied"] == [1]
    assert body["rejected"] == []
    assert body["high_water"] == 1


def test_events_unknown_kind_lands_in_rejected(client: TestClient) -> None:
    ack = client.post(
        "/api/fleet/events", json={"runner_id": "r1", "facts": [{"seq": 1, "kind": "made.up", "payload": {}}]}
    )
    assert ack.status_code == 200
    body = ack.json()
    assert body["applied"] == []
    assert body["rejected"] == [1]
    assert body["already_applied"] == []
    assert body["high_water"] == 0  # the mark does not advance past a rejected seq


def test_events_already_applied_idempotency_on_a_replayed_seq(client: TestClient) -> None:
    chunk_id = _seed(client)
    client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})
    first = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    assert first.json()["applied"] == [1]
    replayed = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 99}}],
        },
    )
    assert replayed.json()["already_applied"] == [1]
    assert replayed.json()["applied"] == []
    # the replayed epoch is discarded, not re-applied — the fence stays at 1.
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["latest_epoch"] == 1


# --- test-control answer route ------------------------------------------------


def test_seed_answer_makes_the_question_poll_return_answered(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "q1",
                        "chunk_id": chunk_id,
                        "epoch": 1,
                        "question": "merge?",
                        "asked_at": "2026-07-13T00:00:00+00:00",
                    },
                }
            ],
        },
    )
    resp = client.post("/_seed/answer", json={"question_id": "q1", "answer": "yes", "answered_by": "ops"})
    assert resp.status_code == 200
    assert resp.json() == {"answered": True, "question_id": "q1"}

    polled = client.get("/api/fleet/questions/q1").json()
    assert polled["answered"] is True
    assert polled["answer"] == "yes"
    assert polled["answered_by"] == "ops"
    assert polled["answered_at"] is not None


def test_seed_answer_404s_on_unknown_question(client: TestClient) -> None:
    resp = client.post("/_seed/answer", json={"question_id": "unknown", "answer": "yes"})
    assert resp.status_code == 404


# --- the session declaration on the envelope (issues #115, #144) -------------
#
# `bzh:wire-change-extends-mock`: the mock's `NodeConfig` mirrors the real hub's, so a
# real runner deserializes its replies unchanged. It never picked up `session_source`
# when #115 landed, so it could not drive a targeted-resume envelope at all — cleared
# here alongside #144's declaration fields.


_SESSION_SPEC = {
    "entry": "build",
    "nodes": {
        "build": {
            "executor": "runner",
            "prompt": "b",
            "judgement_prompt": "j",
            "session": "fresh",
            "session_source": "code",
            "session_name": "code",
            "session_model": ["blizzard:basic", "gpt-5.3-codex"],
            "session_effort": "medium",
            "session_rotate": {"max_context_tokens": 120000, "max_invocations": 30},
            "choices": [{"name": "pass", "description": "p", "to": "review"}],
        },
        "review": {
            "executor": "runner",
            "prompt": "r",
            "judgement_prompt": "j",
            "session": "resume",
            "session_source": "code",
            "session_name": "code",
            "session_model": ["blizzard:basic"],
            "choices": [{"name": "pass", "description": "p", "to": "done"}],
        },
    },
    "work_refs": [{"source": "o-r", "ref": "1"}],
}


def test_a_seeded_session_declaration_rides_the_claim_envelope(client: TestClient) -> None:
    resp = client.post("/_seed/chunk", json=_SESSION_SPEC)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]

    claim = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert claim.status_code == 201, claim.text
    node = claim.json()["envelope"]["node"]
    assert node["session"] == "fresh"
    assert node["session_source"] == "code"
    assert node["session_name"] == "code"
    assert node["session_model"] == ["blizzard:basic", "gpt-5.3-codex"]
    assert node["session_effort"] == "medium"
    assert node["session_rotate"] == {
        "max_context_tokens": 120000,
        "max_transcript_bytes": None,
        "max_invocations": 30,
    }


def test_the_declaration_rides_the_next_envelope_and_the_idempotent_re_read(client: TestClient) -> None:
    resp = client.post("/_seed/chunk", json=_SESSION_SPEC)
    chunk_id = resp.json()["chunk_id"]
    _claim_and_fence(client, chunk_id)

    step = client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": "build"},
    )

    node = step.json()["next_envelope"]["node"]
    assert (node["node_name"], node["session"], node["session_name"]) == ("review", "resume", "code")
    assert node["session_rotate"] is None  # `review` declares no bounds

    re_read = client.get(f"/api/fleet/chunks/{chunk_id}/envelope").json()
    assert re_read["node"]["session_name"] == "code"


def test_a_node_declaring_no_session_carries_the_pre_144_shape(client: TestClient) -> None:
    # Every scenario written before #144 — no pool, no preference, nothing bounded.
    chunk_id = _seed(client)

    claim = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    node = claim.json()["envelope"]["node"]
    assert node["session_source"] is None
    assert node["session_name"] is None
    assert node["session_model"] == []
    assert node["session_effort"] is None
    assert node["session_rotate"] is None
