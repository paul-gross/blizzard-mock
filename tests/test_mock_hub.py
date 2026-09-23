"""Unit + component coverage for the mock hub (``blizzard-mock:unit-test``).

Drives the hub-mirror surface over a ``TestClient`` (in-process, no network): the happy
path — seed → peek → claim → fence → complete → a hub node derives ``done`` — plus **each
of the ten levers**, asserting the named edge state a runner-under-test would then have
to survive, and the full ``/events`` fact vocabulary (blizzard-mock#4). No ``blizzard``
import: the mock stands alone.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.domain.service import _TRANSCRIPT_RECORD_MAX_BYTES, MockHubService

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
        "deliver": {"executor": "hub"},
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


def test_chunk_statuses_batches_reads_deduping_and_omitting_unknown_ids(client: TestClient) -> None:
    """blizzard#521: the slim batch projection — de-dupes preserving order, silently
    omits an unknown id, never a 404."""
    claimed_id = _seed(client)
    assert client.post("/api/fleet/routes", json={"chunk_id": claimed_id, "runner_id": "r1"}).status_code == 201
    unclaimed_id = _seed(client)

    resp = client.get(
        "/api/fleet/chunk-statuses",
        params=[
            ("chunk_id", claimed_id),
            ("chunk_id", "no-such-chunk"),
            ("chunk_id", unclaimed_id),
            ("chunk_id", claimed_id),
        ],
    )
    assert resp.status_code == 200
    statuses = resp.json()
    assert [s["chunk_id"] for s in statuses] == [claimed_id, unclaimed_id]  # deduped, unknown omitted, order preserved
    assert statuses[0]["route_runner_id"] == "r1"
    assert statuses[1]["route_runner_id"] is None


def test_chunk_statuses_conflicting_fact_reports_a_foreign_holder(client: TestClient) -> None:
    """blizzard#521: the runner tick now reads routes only through this endpoint, so the
    ``conflicting_fact`` lever must drive its detach/abandon path here too, exactly as it
    already does through ``GET /chunks/{id}``."""
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    # armed single-shot (remaining=1): the conflicting fact surfaces once, then self-expires.
    client.post(
        "/_levers/conflicting_fact",
        json={"chunk_id": chunk_id, "remaining": 1, "payload": {"runner_id": "ghost-runner"}},
    )
    first = client.get("/api/fleet/chunk-statuses", params=[("chunk_id", chunk_id)]).json()
    assert first[0]["route_runner_id"] == "ghost-runner"
    second = client.get("/api/fleet/chunk-statuses", params=[("chunk_id", chunk_id)]).json()
    assert second[0]["route_runner_id"] == "r1"


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


def _registered_capabilities(client: TestClient, runner_id: str) -> tuple:
    """A registered runner's stored capability snapshot, read from the composition root's
    own service directly — the domain-core dataclass shape, not the wire view."""
    service: MockHubService = client.app.state.service  # type: ignore[attr-defined]
    row = service._state.get_runner(runner_id)
    assert row is not None
    return row.capabilities


def test_registration_accepts_and_stores_capabilities(client: TestClient) -> None:
    """blizzard#433 — the runner's capability snapshot round-trips into the stored
    registry row, mirroring the real hub's own registration write."""
    reg = client.post(
        "/api/fleet/runners",
        json={
            "runner_id": "r-cap",
            "workspace_id": "ws",
            "capabilities": [
                {"harness_id": "claude_code", "version": "1.2.3", "tiers": ["blizzard:frontier"], "default": True}
            ],
        },
    )
    assert reg.status_code == 201, reg.text

    capabilities = _registered_capabilities(client, "r-cap")
    assert len(capabilities) == 1
    capability = capabilities[0]
    assert capability.harness_id == "claude_code"
    assert capability.version == "1.2.3"
    assert capability.tiers == ("blizzard:frontier",)
    assert capability.default is True


def test_runner_view_carries_the_registered_capability_snapshot(client: TestClient) -> None:
    """blizzard#441 — ``RunnerView`` mirrors the real hub's ``capabilities`` field, so the
    board can read it back the same way it reads liveness or the pause brakes."""
    client.post(
        "/api/fleet/runners",
        json={
            "runner_id": "r-cap-view",
            "workspace_id": "ws",
            "capabilities": [
                {"harness_id": "claude_code", "version": "1.2.3", "tiers": ["blizzard:frontier"], "default": True}
            ],
        },
    )

    resp = client.get("/api/fleet/runners/r-cap-view")
    assert resp.status_code == 200, resp.text
    assert resp.json()["capabilities"] == [
        {
            "harness_id": "claude_code",
            "version": "1.2.3",
            "tiers": ["blizzard:frontier"],
            "default": True,
            "available": True,
        }
    ]


def test_runner_view_has_no_capabilities_when_none_were_registered(client: TestClient) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r-no-cap-view", "workspace_id": "ws"})
    resp = client.get("/api/fleet/runners/r-no-cap-view")
    assert resp.status_code == 200, resp.text
    assert resp.json()["capabilities"] == []


def test_registration_without_capabilities_leaves_it_empty(client: TestClient) -> None:
    """A request predating blizzard#433 parses unchanged — the empty-default convention
    every earlier optional registration field already uses."""
    assert client.post("/api/fleet/runners", json={"runner_id": "r-no-cap", "workspace_id": "ws"}).status_code == 201
    assert _registered_capabilities(client, "r-no-cap") == ()


def test_reregistration_replaces_the_capability_snapshot_whole(client: TestClient) -> None:
    """Unconditional overwrite (blizzard#433), like ``redirect_uris``: a re-registration
    dropping a binding leaves no trace of it."""
    client.post(
        "/api/fleet/runners",
        json={
            "runner_id": "r-cap-2",
            "workspace_id": "ws",
            "capabilities": [{"harness_id": "claude_code", "tiers": ["blizzard:basic"], "default": True}],
        },
    )
    assert len(_registered_capabilities(client, "r-cap-2")) == 1

    client.post("/api/fleet/runners", json={"runner_id": "r-cap-2", "workspace_id": "ws"})
    assert _registered_capabilities(client, "r-cap-2") == ()


# --- matched fleet peek (blizzard#433 Phase 3) -------------------------------


def _seed_harness_chunk(client: TestClient, harness_id: str) -> str:
    """A one-node chunk whose entry demands ``harness_id`` alone — ineligible for any
    capability snapshot that does not advertise it."""
    resp = client.post(
        "/_seed/chunk",
        json={
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "prompt": "b",
                    "judgement_prompt": "j",
                    "session_harnesses": [harness_id],
                    "choices": [{"name": "pass", "description": "p", "to": "done"}],
                }
            },
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _register(client: TestClient, runner_id: str = "r1") -> None:
    assert client.post("/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": "ws"}).status_code == 201


_DEFAULT_CAPABILITY = [{"harness_id": "claude_code", "default": True}]


def test_matched_peek_pass_over_skips_an_ineligible_head_and_returns_the_first_workable_entry(
    client: TestClient,
) -> None:
    ineligible = _seed_harness_chunk(client, "special_harness")
    workable = _seed(client)
    _register(client)

    resp = client.post("/api/fleet/queue/peek", params={"runner_id": "r1"}, json={"capabilities": _DEFAULT_CAPABILITY})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert [e["chunk_id"] for e in entries] == [workable]
    assert entries[0]["position"] == 1  # the skipped head keeps its own position
    assert entries[0]["blocked"] is None

    # neither chunk moved — the order itself is never reshaped.
    assert [e["chunk_id"] for e in client.get("/api/fleet/queue/peek").json()["entries"]] == [ineligible, workable]


def test_matched_peek_hold_yields_nothing_when_the_head_is_unusable(client: TestClient) -> None:
    _seed_harness_chunk(client, "special_harness")
    _seed(client)
    _register(client)

    resp = client.post(
        "/api/fleet/queue/peek",
        params={"runner_id": "r1"},
        json={"capabilities": _DEFAULT_CAPABILITY, "policy": "hold"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["entries"] == []


def test_matched_peek_hold_returns_a_usable_head_normally(client: TestClient) -> None:
    workable = _seed(client)
    _register(client)

    resp = client.post(
        "/api/fleet/queue/peek",
        params={"runner_id": "r1"},
        json={"capabilities": _DEFAULT_CAPABILITY, "policy": "hold"},
    )
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert [e["chunk_id"] for e in entries] == [workable]
    assert entries[0]["position"] == 0


def test_matched_peek_no_capabilities_asserted_applies_no_capability_filter(client: TestClient) -> None:
    """An empty ``capabilities`` list applies no capability filter — the head entry is
    returned unfiltered, even though it names a harness the (empty) snapshot cannot
    satisfy."""
    head = _seed_harness_chunk(client, "special_harness")
    _register(client)

    resp = client.post("/api/fleet/queue/peek", params={"runner_id": "r1"}, json={})
    assert resp.status_code == 200, resp.text
    assert [e["chunk_id"] for e in resp.json()["entries"]] == [head]


def test_matched_peek_an_unrecognized_policy_value_round_trips_as_pass_over(client: TestClient) -> None:
    _seed_harness_chunk(client, "special_harness")  # the head, skipped rather than absent
    workable = _seed(client)
    _register(client)

    resp = client.post(
        "/api/fleet/queue/peek",
        params={"runner_id": "r1"},
        json={"capabilities": _DEFAULT_CAPABILITY, "policy": "not-a-real-policy"},
    )
    assert resp.status_code == 200, resp.text
    assert [e["chunk_id"] for e in resp.json()["entries"]] == [workable]


def test_matched_peek_the_blocked_dimension_takes_the_same_policy_as_the_capability_one(client: TestClient) -> None:
    blocked = _seed(client)
    workable = _seed(client)
    _register(client)
    client.post("/_levers/dependency_unmet", json={"chunk_id": blocked, "payload": {"prerequisite_chunk_id": "ch_x"}})

    pass_over = client.post("/api/fleet/queue/peek", params={"runner_id": "r1"}, json={})
    assert [e["chunk_id"] for e in pass_over.json()["entries"]] == [workable]

    hold = client.post("/api/fleet/queue/peek", params={"runner_id": "r1"}, json={"policy": "hold"})
    assert hold.json()["entries"] == []

    # the peek's own read never consumes the lever (mirrors GET /queue/peek, blizzard#459).
    still_blocked = client.post("/api/fleet/queue/peek", params={"runner_id": "r1"}, json={"policy": "hold"})
    assert still_blocked.json()["entries"] == []


def test_matched_peek_refuses_401_without_a_runner_id(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/fleet/queue/peek", json={})
    assert resp.status_code == 401, resp.text


def test_matched_peek_refuses_401_for_an_unregistered_runner_id(client: TestClient) -> None:
    _seed(client)
    resp = client.post("/api/fleet/queue/peek", params={"runner_id": "ghost"}, json={})
    assert resp.status_code == 401, resp.text


def test_matched_peek_leaves_the_legacy_verb_unfiltered(client: TestClient) -> None:
    """The legacy ``GET`` keeps returning the full, unfiltered order regardless of the
    matched verb's own filtering — no shared state between the two beyond the queue
    itself."""
    ineligible = _seed_harness_chunk(client, "special_harness")
    workable = _seed(client)
    _register(client)
    client.post("/api/fleet/queue/peek", params={"runner_id": "r1"}, json={"capabilities": _DEFAULT_CAPABILITY})

    legacy = client.get("/api/fleet/queue/peek").json()["entries"]
    assert [e["chunk_id"] for e in legacy] == [ineligible, workable]


# --- claim revalidation (blizzard#433 Phase 4) -------------------------------


def _register_with_capabilities(client: TestClient, capabilities: list[dict], *, runner_id: str = "r1") -> None:
    resp = client.post(
        "/api/fleet/runners", json={"runner_id": runner_id, "workspace_id": "ws", "capabilities": capabilities}
    )
    assert resp.status_code == 201, resp.text


def test_claim_denied_when_stored_capabilities_no_longer_satisfy_the_chunk(client: TestClient) -> None:
    """blizzard#433 D9: a claim is refused with a 409 distinct from ``ClaimConflict`` and
    ``DependencyUnmet`` when the runner's *stored* registration cannot run the chunk's
    reachable lineage — the route is never minted."""
    chunk_id = _seed_harness_chunk(client, "special_harness")
    _register_with_capabilities(client, _DEFAULT_CAPABILITY)  # advertises "claude_code", not "special_harness"

    denied = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert denied.status_code == 409, denied.text
    body = denied.json()
    assert body["chunk_id"] == chunk_id
    assert body["incompatible_runner_id"] == "r1"
    assert "held_by_runner_id" not in body
    assert "prerequisite_chunk_id" not in body
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["route"] is None


def test_claim_allowed_when_stored_capabilities_satisfy_the_chunk(client: TestClient) -> None:
    chunk_id = _seed(client)  # no declared harness preference — a default capability satisfies it
    _register_with_capabilities(client, _DEFAULT_CAPABILITY)

    claimed = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert claimed.status_code == 201, claimed.text
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["route"]["runner_id"] == "r1"


def test_claim_with_no_registered_capabilities_is_never_revalidated(client: TestClient) -> None:
    """A registration reporting no capabilities at all skips the check entirely — the
    previous-minor / never-re-registered case never meets a denial it has no branch for."""
    chunk_id = _seed_harness_chunk(client, "special_harness")
    _register(client)  # no capabilities field at all

    claimed = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert claimed.status_code == 201, claimed.text


def test_claim_denied_once_capabilities_regress_between_registration_and_claim(client: TestClient) -> None:
    """The peek-then-claim skew window: the claim re-reads the stored registration fresh
    rather than trusting a once-true snapshot."""
    chunk_id = _seed_harness_chunk(client, "special_harness")
    _register_with_capabilities(client, [{"harness_id": "special_harness", "default": True}])

    _register_with_capabilities(client, _DEFAULT_CAPABILITY)  # capability change lands before the claim
    denied = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert denied.status_code == 409, denied.text
    assert denied.json()["incompatible_runner_id"] == "r1"


# --- levers -----------------------------------------------------------------


def test_lever_catalog_lists_all_ten(client: TestClient) -> None:
    catalog = client.get("/_levers").json()["catalog"]
    assert set(catalog) == {
        "delay",
        "delay_transcripts",
        "drop_ack",
        "conflicting_fact",
        "unreachable",
        "unreachable_transcripts",
        "replay",
        "stale_envelope",
        "chunk_unknown",
        "dependency_unmet",
    }


def test_lever_unreachable_heals_mid_lease(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    assert client.post("/_levers/unreachable", json={"remaining": 2}).status_code == 200
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 503
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 503
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 200  # healed after 2


def test_lever_unreachable_transcripts_is_scoped_to_its_own_route(client: TestClient) -> None:
    """D6: a wedged transcript flush never blocks the fact lane — the lever fails
    ``/transcripts`` alone, and ``/events`` (and everything else) stays healthy."""
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    assert client.post("/_levers/unreachable_transcripts", json={}).status_code == 200

    failed = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": []})
    assert failed.status_code == 503

    healthy = client.post("/api/fleet/events", json={"runner_id": "r1", "facts": []})
    assert healthy.status_code == 200
    assert client.get(f"/api/fleet/chunks/{chunk_id}/envelope").status_code == 200


def test_lever_delay_transcripts_is_scoped_to_its_own_route(client: TestClient) -> None:
    """review F18: D6's lane-independence claim is "a wedged OR SLOW transcript flush
    never blocks the fact lane" — ``unreachable_transcripts`` above only ever proves the
    hard-down half. This lever proves the slow half: ``/transcripts`` alone sleeps,
    ``/events`` (and everything else) stays fast."""
    import time

    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    # Global, not chunk-scoped (matching `unreachable_transcripts` above): the transcripts
    # route's path carries no `chunks/{id}` segment at all (`chunk_id` lives in each
    # record's own JSON body), so a per-chunk-scoped lever could never match it.
    client.post("/_levers/delay_transcripts", json={"payload": {"ms": 200}})

    started = time.monotonic()
    slow = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": []})
    assert time.monotonic() - started >= 0.18
    assert slow.status_code == 200  # delayed, not failed

    started = time.monotonic()
    fast = client.post("/api/fleet/events", json={"runner_id": "r1", "facts": []})
    assert time.monotonic() - started < 0.1
    assert fast.status_code == 200


@pytest.mark.asyncio
async def test_lever_delay_transcripts_does_not_block_concurrent_requests() -> None:
    """review F12: a synchronous sleep in the middleware would hold the whole event loop —
    a concurrent, undelayed request would then wait out the delay too, not just the one
    lever-targeted route."""
    import asyncio
    import time

    from httpx import ASGITransport, AsyncClient

    app = create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC)))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        await ac.post("/_levers/delay_transcripts", json={"payload": {"ms": 300}})
        started = time.monotonic()
        fast_finished_at = None

        async def fast_call():  # type: ignore[no-untyped-def]
            nonlocal fast_finished_at
            resp = await ac.post("/api/fleet/events", json={"runner_id": "r1", "facts": []})
            fast_finished_at = time.monotonic() - started
            return resp

        slow, fast = await asyncio.gather(
            ac.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": []}), fast_call()
        )

    assert slow.status_code == 200
    assert fast.status_code == 200
    assert fast_finished_at is not None
    # A blocking sleep would freeze the whole loop, so this undelayed request could not
    # complete until the delay elapsed too.
    assert fast_finished_at < 0.15


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


def test_lever_dependency_unmet_denies_the_claim_naming_the_prerequisite(client: TestClient) -> None:
    """blizzard#458: a claim on a chunk the lever names as depending on an unmet
    prerequisite is refused with a 409 shape distinct from ``ClaimConflict`` — no
    ``held_by_runner_id``, a ``prerequisite_chunk_id`` instead."""
    chunk_id = _seed(client)
    client.post(
        "/_levers/dependency_unmet",
        json={"chunk_id": chunk_id, "payload": {"prerequisite_chunk_id": "ch_prereq"}},
    )

    denied = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert denied.status_code == 409, denied.text
    body = denied.json()
    assert body["chunk_id"] == chunk_id
    assert body["prerequisite_chunk_id"] == "ch_prereq"
    assert "held_by_runner_id" not in body
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["route"] is None


def test_lever_dependency_unmet_is_sticky_until_cleared(client: TestClient) -> None:
    """Unlike a one-shot fact, an unmet dependency stands until the operator clears it —
    the lever stays armed with no ``remaining`` set, so it denies every claim attempt."""
    chunk_id = _seed(client)
    client.post(
        "/_levers/dependency_unmet",
        json={"chunk_id": chunk_id, "payload": {"prerequisite_chunk_id": "ch_prereq"}},
    )

    assert client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 409
    assert client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 409

    client.delete("/_levers/dependency_unmet", params={"chunk_id": chunk_id})
    assert client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"}).status_code == 201


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
    assert detail["escalation"] == {
        "epoch": 1,
        "takeover_command": "blizzard runner takeover ch_1",
        "wrapped_takeover_command": "",
    }
    assert client.post("/api/fleet/chunks/unknown/escalations", json={"epoch": 1, "runner_id": "r1"}).status_code == 404


def test_report_escalation_direct_route_carries_the_wrapped_takeover_command(client: TestClient) -> None:
    """The DIRECT, non-buffered ``POST .../escalations`` route (issue #251) — the
    counterpart to the batched ``/events`` case below."""
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    resp = client.post(
        f"/api/fleet/chunks/{chunk_id}/escalations",
        json={
            "epoch": 1,
            "runner_id": "r1",
            "takeover_command": "cd <workdir> && claude --resume abc",
            "wrapped_takeover_command": f"blizzard runner takeover {chunk_id} --dir /runner",
        },
    )
    assert resp.status_code == 202
    detail = client.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert detail["escalation"] == {
        "epoch": 1,
        "takeover_command": "cd <workdir> && claude --resume abc",
        "wrapped_takeover_command": f"blizzard runner takeover {chunk_id} --dir /runner",
    }


def test_hub_advance_completes_a_chunk_parked_at_the_entry_hub_node(client: TestClient) -> None:
    resp = client.post("/_seed/chunk", json={"entry": "deliver", "nodes": {"deliver": {"executor": "hub"}}})
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
                    "payload": {
                        "chunk_id": chunk_id,
                        "epoch": 1,
                        "takeover_command": "take it over",
                        "wrapped_takeover_command": f"blizzard runner takeover {chunk_id} --dir /runner",
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [2]
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["escalation"] == {
        "epoch": 1,
        "takeover_command": "take it over",
        "wrapped_takeover_command": f"blizzard runner takeover {chunk_id} --dir /runner",
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


def test_a_report_that_outruns_its_registration_is_readable_once_it_lands(client: TestClient) -> None:
    """The outbound buffer replays an outage in FIFO order, so a self-report can legitimately
    reach the hub before the registration that follows it. The real hub persists both kinds
    without a known runner; acking and discarding would make that ordering untestable."""
    for seq, kind, payload in (
        (1, "runner.locally_paused", {"by": "op", "reason": "maintenance"}),
        (
            2,
            "external_subscription_usage.sampled",
            {"slug": "anthropic", "sampled_at": "2026-08-01T12:00:00+00:00", "windows": []},
        ),
    ):
        ack = client.post(
            "/api/fleet/events",
            json={"runner_id": "late", "facts": [{"seq": seq, "kind": kind, "payload": payload}]},
        )
        assert ack.json()["applied"] == [seq]

    client.post("/api/fleet/runners", json={"runner_id": "late", "workspace_id": "ws"})

    view = client.get("/api/fleet/runners/late").json()
    assert view["locally_paused"] is True
    assert view["locally_paused_by"] == "op"
    assert view["subscriptions"] == [
        {
            "slug": "anthropic",
            "name": "anthropic",
            "sampled_at": "2026-08-01T12:00:00+00:00",
            "windows": [],
            "condition": None,
        }
    ]


def test_malformed_usage_windows_are_omitted_and_a_later_valid_empty_sample_renders(client: TestClient) -> None:
    """The mock drops invalid windows while accepting the advisory sample, like the real hub."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    valid_window = {
        "window": "5h",
        "utilization_pct": 42.5,
        "resets_at": "2026-08-01T17:00:00+00:00",
        "window_seconds": 18000,
    }
    malformed = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "anthropic",
                        "name": "Anthropic",
                        "sampled_at": "2026-08-01T12:00:00+00:00",
                        "windows": [
                            valid_window,
                            {"window": "7d", "utilization_pct": 10.0},
                            {
                                "window": "1h",
                                "utilization_pct": "not-a-number",
                                "resets_at": "2026-08-01T13:00:00+00:00",
                                "window_seconds": 3600,
                            },
                            {
                                "window": "daily",
                                "utilization_pct": 20.0,
                                "resets_at": "not-an-instant",
                                "window_seconds": 86400,
                            },
                            {
                                "window": "zero",
                                "utilization_pct": 20.0,
                                "resets_at": "2026-08-01T12:00:00+00:00",
                                "window_seconds": 0,
                            },
                        ],
                    },
                }
            ],
        },
    )
    assert malformed.json()["applied"] == [1]

    first_read = client.get("/api/fleet/runners/r1")
    assert first_read.status_code == 200
    assert first_read.json()["subscriptions"][0]["windows"] == [valid_window]

    healthy = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "anthropic",
                        "name": "Anthropic",
                        "sampled_at": "2026-08-01T12:01:00+00:00",
                        "windows": [],
                    },
                }
            ],
        },
    )
    assert healthy.json()["applied"] == [2]

    assert client.get("/api/fleet/runners/r1").json()["subscriptions"] == [
        {
            "slug": "anthropic",
            "name": "Anthropic",
            "sampled_at": "2026-08-01T12:01:00+00:00",
            "windows": [],
            "condition": None,
        }
    ]


@pytest.mark.parametrize(
    ("sampled_at", "expected"),
    [
        ("not-an-instant", "2026-07-13T00:00:00+00:00"),
        ("2026-08-01T12:00:00", "2026-08-01T12:00:00+00:00"),
        ("2026-08-01T14:00:00+02:00", "2026-08-01T12:00:00+00:00"),
    ],
)
def test_sampled_at_matches_the_real_hubs_utc_and_fallback_semantics(
    client: TestClient, sampled_at: str, expected: str
) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    response = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {"slug": "anthropic", "sampled_at": sampled_at, "windows": []},
                }
            ],
        },
    )

    assert response.json()["applied"] == [1]
    assert client.get("/api/fleet/runners/r1").json()["subscriptions"][0]["sampled_at"] == expected


@pytest.mark.parametrize(
    "invalid_pct",
    [float("nan"), float("inf"), float("-inf"), -0.1, 100.1],
    ids=["nan", "inf", "-inf", "below", "above"],
)
def test_non_finite_or_out_of_range_utilization_windows_are_omitted_at_ingest(
    client: TestClient, invalid_pct: float
) -> None:
    service = client.app.state.service  # type: ignore[attr-defined]
    valid_window = {
        "window": "5h",
        "utilization_pct": 42.5,
        "resets_at": "2026-08-01T17:00:00+00:00",
        "window_seconds": 18_000,
    }

    ack = service.ingest_facts(
        "r1",
        [
            {
                "seq": 1,
                "kind": "external_subscription_usage.sampled",
                "payload": {
                    "slug": "anthropic",
                    "sampled_at": "2026-08-01T12:00:00+00:00",
                    "windows": [
                        valid_window,
                        {
                            "window": "7d",
                            "utilization_pct": invalid_pct,
                            "resets_at": "2026-08-08T12:00:00+00:00",
                            "window_seconds": 604_800,
                        },
                    ],
                },
            }
        ],
    )

    assert ack.applied == [1]
    assert client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"}).status_code == 201
    windows = client.get("/api/fleet/runners/r1").json()["subscriptions"][0]["windows"]
    assert windows == [valid_window]
    json.dumps(windows, allow_nan=False)


def test_events_event_recorded_is_accepted(client: TestClient) -> None:
    """An operational event (issue #125) is never token-gated and needs no chunk — it
    lands off a runner-scoped batch with no claim in play."""
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "event.recorded",
                    "payload": {"severity": "error", "kind": "spawn.failed", "message": "no environment free"},
                }
            ],
        },
    )
    assert ack.json()["applied"] == [1]
    assert ack.json()["rejected"] == []


def test_events_newest_external_usage_sample_wins_on_its_subscription(client: TestClient) -> None:
    """The newest sample overwrites the earlier sample for its slug."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    for seq, pct in ((1, 42.0), (2, 71.5)):
        ack = client.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [
                    {
                        "seq": seq,
                        "kind": "external_subscription_usage.sampled",
                        "payload": {
                            "slug": "anthropic",
                            "sampled_at": f"2026-08-01T1{seq}:00:00+00:00",
                            "windows": [
                                {
                                    "window": "5h",
                                    "utilization_pct": pct,
                                    "resets_at": "2026-08-01T17:00:00+00:00",
                                    "window_seconds": 18000,
                                }
                            ],
                        },
                    }
                ],
            },
        )
        assert ack.json()["applied"] == [seq]
    view = client.get("/api/fleet/runners/r1").json()
    subscription = view["subscriptions"][0]
    assert subscription["sampled_at"] == "2026-08-01T12:00:00+00:00"
    assert subscription["windows"][0]["utilization_pct"] == 71.5


def test_registration_round_trips_env_capacity_onto_the_runner_view(client: TestClient) -> None:
    """``env_capacity`` is reported at registration and overwritten on every re-register,
    exactly like ``workspace_id``."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws", "env_capacity": 4})
    assert client.get("/api/fleet/runners/r1").json()["env_capacity"] == 4
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws", "env_capacity": 2})
    assert client.get("/api/fleet/runners/r1").json()["env_capacity"] == 2


def test_events_external_subscription_usage_sampled_is_accepted(client: TestClient) -> None:
    """A sampled external-subscription-usage snapshot (issue #218) is runner-scoped and
    advisory-only: applied for a runner the registry has never seen. That it survives to be
    read is pinned by ``test_a_report_that_outruns_its_registration_is_readable_once_it_lands``."""
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "anthropic",
                        "sampled_at": "2026-08-01T12:00:00+00:00",
                        "windows": [
                            {
                                "window": "5h",
                                "utilization_pct": 42.0,
                                "resets_at": "2026-08-01T17:00:00+00:00",
                                "window_seconds": 18000,
                            }
                        ],
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [1]
    assert ack.json()["rejected"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"sampled_at": "2026-08-01T12:00:00+00:00", "windows": []},
        {"slug": "", "sampled_at": "2026-08-01T12:00:00+00:00", "windows": []},
        {"slug": 123, "sampled_at": "2026-08-01T12:00:00+00:00", "windows": []},
    ],
    ids=["missing-slug", "empty-slug", "non-string-slug"],
)
def test_events_invalid_external_usage_sample_is_rejected_without_a_subscription(
    client: TestClient, payload: dict[str, object]
) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})

    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": payload,
                }
            ],
        },
    )

    assert ack.json()["rejected"] == [1]
    assert client.get("/api/fleet/runners/r1").json()["subscriptions"] == []


# blizzard#504 D7 — the `missed` fact and its `condition` derivation.
# --------------------------------------------------------------------------- #


def test_events_external_subscription_usage_missed_is_accepted(client: TestClient) -> None:
    """A missed-fact report (blizzard#504 D7) is runner-scoped and advisory-only, mirroring
    its sampled sibling — applied for a runner the registry has never seen."""
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "missed_at": "2026-08-01T12:00:00+00:00",
                        "reason": "credential_lapsed",
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [1]
    assert ack.json()["rejected"] == []


@pytest.mark.parametrize(
    "payload",
    [
        {"missed_at": "2026-08-01T12:00:00+00:00", "reason": "credential_lapsed"},
        {"slug": "", "missed_at": "2026-08-01T12:00:00+00:00", "reason": "credential_lapsed"},
        {"slug": 123, "missed_at": "2026-08-01T12:00:00+00:00", "reason": "credential_lapsed"},
    ],
    ids=["missing-slug", "empty-slug", "non-string-slug"],
)
def test_events_invalid_external_usage_miss_is_rejected_without_a_subscription(
    client: TestClient, payload: dict[str, object]
) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})

    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "external_subscription_usage.missed", "payload": payload}],
        },
    )

    assert ack.json()["rejected"] == [1]
    assert client.get("/api/fleet/runners/r1").json()["subscriptions"] == []


def test_a_miss_with_no_prior_sample_renders_a_miss_only_lapsed_row(client: TestClient) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "missed_at": "2026-08-01T12:00:00+00:00",
                        "reason": "credential_lapsed",
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [1]

    assert client.get("/api/fleet/runners/r1").json()["subscriptions"] == [
        {"slug": "openai", "name": "OpenAI", "sampled_at": None, "windows": [], "condition": "credential_lapsed"}
    ]


def test_a_miss_newer_than_the_sample_supersedes_it_as_a_lapsed_row(client: TestClient) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "sampled_at": "2026-08-01T12:00:00+00:00",
                        "windows": [],
                    },
                }
            ],
        },
    )
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.missed",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "missed_at": "2026-08-01T12:05:00+00:00",
                        "reason": "credential_lapsed",
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [2]

    view = client.get("/api/fleet/runners/r1").json()["subscriptions"]
    assert view == [
        {"slug": "openai", "name": "OpenAI", "sampled_at": None, "windows": [], "condition": "credential_lapsed"}
    ]


def test_a_sample_newer_than_the_miss_clears_the_condition(client: TestClient) -> None:
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "missed_at": "2026-08-01T12:00:00+00:00",
                        "reason": "credential_lapsed",
                    },
                }
            ],
        },
    )
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "sampled_at": "2026-08-01T12:05:00+00:00",
                        "windows": [],
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [2]

    view = client.get("/api/fleet/runners/r1").json()["subscriptions"]
    assert view == [
        {
            "slug": "openai",
            "name": "OpenAI",
            "sampled_at": "2026-08-01T12:05:00+00:00",
            "windows": [],
            "condition": None,
        }
    ]


def test_a_non_lapsed_miss_reason_never_surfaces_as_a_condition(client: TestClient) -> None:
    """Only `credential_lapsed` ever surfaces as `condition` (D7) — every other reason is
    silent, whether or not a sample exists for the same slug."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    ack = client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "missed_at": "2026-08-01T12:00:00+00:00",
                        "reason": "endpoint_unreachable",
                    },
                }
            ],
        },
    )
    assert ack.json()["applied"] == [1]

    # Never sampled, and only a non-lapsed miss on record — simply absent.
    assert client.get("/api/fleet/runners/r1").json()["subscriptions"] == []


def test_a_miss_never_overwrites_the_sample_row_it_supersedes_in_the_view(client: TestClient) -> None:
    """The sample and miss are sibling records (D7) — a later sample after a miss reads
    back the sample's own report, not a value carried over from the miss."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "missed_at": "2026-08-01T12:00:00+00:00",
                        "reason": "credential_lapsed",
                    },
                },
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "openai",
                        "name": "OpenAI",
                        "sampled_at": "2026-08-01T12:05:00+00:00",
                        "windows": [
                            {
                                "window": "5h",
                                "utilization_pct": 12.0,
                                "resets_at": "2026-08-01T17:00:00+00:00",
                                "window_seconds": 18000,
                            }
                        ],
                    },
                },
            ],
        },
    )

    subscriptions = client.get("/api/fleet/runners/r1").json()["subscriptions"]
    assert len(subscriptions) == 1
    assert subscriptions[0]["condition"] is None
    assert subscriptions[0]["windows"][0]["utilization_pct"] == 12.0


def test_two_distinct_subscriptions_render_separately_on_the_subscriptions_collection(client: TestClient) -> None:
    """A runner declaring more than one subscription (blizzard#436 phase 3) — each slug's
    sample lands and renders as its own entry in ``subscriptions``, distinct by slug and name."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    for seq, slug, name, pct in ((1, "anthropic", "Anthropic", 42.0), (2, "openai", "OpenAI", 17.0)):
        ack = client.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [
                    {
                        "seq": seq,
                        "kind": "external_subscription_usage.sampled",
                        "payload": {
                            "slug": slug,
                            "name": name,
                            "sampled_at": "2026-08-01T12:00:00+00:00",
                            "windows": [
                                {
                                    "window": "5h",
                                    "utilization_pct": pct,
                                    "resets_at": "2026-08-01T17:00:00+00:00",
                                    "window_seconds": 18000,
                                }
                            ],
                        },
                    }
                ],
            },
        )
        assert ack.json()["applied"] == [seq]

    view = client.get("/api/fleet/runners/r1").json()
    subscriptions = {s["slug"]: s for s in view["subscriptions"]}
    assert set(subscriptions) == {"anthropic", "openai"}
    assert subscriptions["anthropic"]["name"] == "Anthropic"
    assert subscriptions["openai"]["name"] == "OpenAI"
    assert subscriptions["anthropic"]["windows"][0]["utilization_pct"] == 42.0
    assert subscriptions["openai"]["windows"][0]["utilization_pct"] == 17.0


def test_each_subscription_advances_on_its_own_cadence_independently(client: TestClient) -> None:
    """A later sample for one slug must not touch a sibling slug's stored view (independent
    cadence, blizzard#436 phase 3) — each upsert is keyed on its own slug."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {"slug": "anthropic", "sampled_at": "2026-08-01T12:00:00+00:00", "windows": []},
                },
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {"slug": "openai", "sampled_at": "2026-08-01T12:05:00+00:00", "windows": []},
                },
            ],
        },
    )

    # Only `openai` samples again — `anthropic`'s stored `sampled_at` must not move.
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 3,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {"slug": "openai", "sampled_at": "2026-08-01T12:10:00+00:00", "windows": []},
                }
            ],
        },
    )

    subscriptions = {s["slug"]: s for s in client.get("/api/fleet/runners/r1").json()["subscriptions"]}
    assert subscriptions["anthropic"]["sampled_at"] == "2026-08-01T12:00:00+00:00"
    assert subscriptions["openai"]["sampled_at"] == "2026-08-01T12:10:00+00:00"


def test_a_subscription_with_no_reported_sample_has_no_entry(client: TestClient) -> None:
    """``subscriptions`` holds one entry per slug that has actually reported a sample; a
    declared-but-unreported slug is simply absent, not a blank entry. The stale-sibling
    claim needs a staleness gate the mock applies none of — proven at the real hub instead."""
    client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "ws"})
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {"slug": "anthropic", "sampled_at": "2026-08-01T12:00:00+00:00", "windows": []},
                }
            ],
        },
    )

    view = client.get("/api/fleet/runners/r1").json()
    assert [s["slug"] for s in view["subscriptions"]] == ["anthropic"]


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


# --- transcript segments (blizzard#247) ---------------------------------------


def _transcript_record(
    chunk_id: str, *, seq: int, turn_range_start: int = 0, turn_range_end: int = 0, **overrides: object
) -> dict:
    record: dict = {
        "seq": seq,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": "build",
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": turn_range_start,
        "turn_range_end": turn_range_end,
        "final": False,
        "normalizer_version": "v1",
        "harness_version": "claude-code-1.0",
        "turns": [
            {
                "index": turn_range_start,
                "kind": "asst",
                "timestamp": None,
                "text": "hi",
                "tool": None,
                "thinking_redacted": False,
                "sidechain": None,
                "truncated": False,
            }
        ],
    }
    record.update(overrides)
    return record


def _turn_with_text(text: str, *, index: int = 0) -> dict:
    """A full, validation-passing turn (review F9 — every field is now required)
    carrying just the given oversized ``text``, for cap tests that don't care about
    turn shape."""
    return {
        "index": index,
        "kind": "asst",
        "timestamp": None,
        "text": text,
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def test_transcripts_rejects_a_turn_with_an_unrecognized_shape(client: TestClient) -> None:
    """review F11: ``turns`` used to be a freeform ``list[dict]`` here — a real field
    rename on the wire (e.g. a turn's ``text`` renamed) would have shipped green through
    every `service-test` scenario, since nothing driving the mock ever validated turn
    shape. Now typed field-for-field against ``blizzard.wire.transcript_segment
    .TurnSegmentView`` (``bzh:wire-change-extends-mock``), an unrecognized required field
    (``input_shape`` missing from a ``tool``) 422s instead of silently passing through."""
    chunk_id = _seed(client)
    bad_record = _transcript_record(
        chunk_id, seq=1, turns=[{"index": 0, "kind": "tool", "tool": {"name": "Bash", "input": {}}}]
    )

    resp = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [bad_record]})

    assert resp.status_code == 422


def test_transcripts_accepts_a_tool_call_missing_input_truncated(client: TestClient) -> None:
    """review round 6 F4: ``input_truncated`` is the one deliberate default in
    ``ToolCallSegmentBody`` — mirroring the real hub's own ``ToolCallSegmentView``
    default (round 5's F1 field, made forward-compat in round 6). A record whose tool
    call omits it entirely must still 200, not 422 like a genuine field rename would."""
    chunk_id = _seed(client)
    turn = {
        "index": 0,
        "kind": "tool",
        "timestamp": None,
        "text": "",
        "tool": {
            "name": "Bash",
            "input": {},
            "input_unparsed": None,
            "input_shape": "object",
            "tool_use_id": "tool_1",
            "output": None,
            "output_truncated": False,
            # "input_truncated" deliberately omitted
        },
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }
    record = _transcript_record(chunk_id, seq=1, turns=[turn])

    resp = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [record]})

    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [1]


def test_transcripts_rejects_a_turn_renamed_at_the_top_level(client: TestClient) -> None:
    """review F9: the nested-``tool`` case above is caught by ``ToolCallSegmentBody``'s
    genuinely-required fields alone — every ``TurnSegmentBody`` field used to default,
    so a top-level rename (e.g. ``text``) validated here while the real hub 422s. Now
    every field mirrors ``TurnSegmentView``'s own required-ness."""
    chunk_id = _seed(client)
    renamed_turn = {
        "index": 0,
        "kind": "asst",
        "timestamp": None,
        "content": "hi",  # "text" renamed to "content" — every other required field present
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }
    bad_record = _transcript_record(chunk_id, seq=1, turns=[renamed_turn])

    resp = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [bad_record]})

    assert resp.status_code == 422


def test_transcripts_rejects_a_record_field_renamed_around_the_turns(client: TestClient) -> None:
    """review F9, the enclosing level: tightening only the turn bodies leaves
    ``TranscriptSegmentRecordBody``'s own ``final``/``normalizer_version``/
    ``harness_version``/``turns`` defaulted, so a rename of one of THOSE still validates
    here while the real hub 422s — the same silent-drift hole one frame out."""
    chunk_id = _seed(client)
    bad_record = _transcript_record(chunk_id, seq=1)
    bad_record["normalizer_name"] = bad_record.pop("normalizer_version")  # renamed on the wire

    resp = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [bad_record]})

    assert resp.status_code == 422


def test_transcripts_lands_records_on_its_own_lane(client: TestClient) -> None:
    """The transcript lane's high-water is independent of the fact lane's (D7): a
    transcript push does not disturb a fact-lane seq already applied, and vice versa."""
    chunk_id = _seed(client)
    client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )

    ack = client.post(
        "/api/fleet/transcripts", json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1)]}
    )
    assert ack.status_code == 200, ack.text
    body = ack.json()
    assert body["applied"] == [1]
    assert body["high_water"] == 1
    assert body["capped"] == []


def test_transcripts_already_applied_idempotency_on_a_replayed_seq(client: TestClient) -> None:
    chunk_id = _seed(client)
    payload = {"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1)]}

    first = client.post("/api/fleet/transcripts", json=payload)
    assert first.json()["applied"] == [1]

    replayed = client.post("/api/fleet/transcripts", json=payload)
    assert replayed.json()["already_applied"] == [1]
    assert replayed.json()["applied"] == []


def test_transcripts_over_cap_record_is_capped_but_acked_and_the_mark_still_advances_past_it(
    client: TestClient,
) -> None:
    """review F8, blizzard#246: mirrors the real hub's ``TranscriptIngestService._apply``
    (blizzard#247) — an over-cap record is capped (never applied, never stored), but the
    high-water mark still advances past it (D6), unlike an unseen seq. Without this, no mock
    or fake ever populates a transcript ack's ``capped`` list, so no tier can catch a
    regression in the runner drain's own cap-handling."""
    chunk_id = _seed(client)
    huge_turns = [_turn_with_text("x" * (_TRANSCRIPT_RECORD_MAX_BYTES + 1000))]

    first = client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1, turns=huge_turns)]},
    )
    assert first.json()["capped"] == [1]
    assert first.json()["high_water"] == 1  # advances past a capped record too (D6)

    # A replay of the same now-behind-the-mark seq still reports its cap outcome, mirroring
    # the real hub's natural-key (D8) lookup on the already-applied path: a runner that
    # crashed in its own after-submit.before-ack window must still learn the record was
    # capped, or the segment-field/warning marking is permanently skipped on retry.
    replay = client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1, turns=huge_turns)]},
    )
    assert replay.json()["capped"] == [1]
    assert replay.json()["already_applied"] == []

    # A LATER, in-cap record in the same batch as a cap still applies.
    ack = client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [
                _transcript_record(chunk_id, seq=2, turn_range_start=1, turn_range_end=1, turns=huge_turns),
                _transcript_record(chunk_id, seq=3, turn_range_start=2, turn_range_end=2),
            ],
        },
    )
    body = ack.json()
    assert body["capped"] == [2]
    assert body["applied"] == [3]
    assert body["high_water"] == 3

    # An ordinary, never-capped seq replays as plain idempotency — the cap outcome above
    # is keyed on the record, not blanket-applied to everything behind the mark.
    clean_replay = client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [_transcript_record(chunk_id, seq=3, turn_range_start=2, turn_range_end=2)],
        },
    )
    assert clean_replay.json()["already_applied"] == [3]
    assert clean_replay.json()["capped"] == []


def test_transcripts_chunk_budget_cap_rejects_independently_of_the_record_cap(client: TestClient) -> None:
    """blizzard#247's second, independent cap: bytes accumulate per chunk, and a record that
    is well within the per-record cap can still be capped once the chunk's own 64 MB budget
    is spent — mirrors the real hub's ``_reject_reason`` checking both caps. Loops until the
    budget actually tips rather than hardcoding a record count against JSON-overhead math."""
    chunk_id = _seed(client)
    big_turns = [_turn_with_text("x" * (2 * 1024 * 1024))]  # under the per-record cap alone
    seq = 0
    capped_seq: int | None = None
    while capped_seq is None:
        seq += 1
        ack = client.post(
            "/api/fleet/transcripts",
            json={
                "runner_id": "r1",
                "records": [
                    _transcript_record(
                        chunk_id, seq=seq, turn_range_start=seq - 1, turn_range_end=seq - 1, turns=big_turns
                    )
                ],
            },
        )
        body = ack.json()
        assert body["high_water"] == seq  # every record advances the mark, applied or capped (D6)
        if body["capped"]:
            capped_seq = seq
        else:
            assert body["applied"] == [seq]
    assert capped_seq <= 40  # sanity: the budget must actually bind within a handful of 2 MB records


def test_transcripts_a_capped_key_re_offered_and_accepted_stops_reporting_capped(client: TestClient) -> None:
    """review round 6 F10: the real hub's `update_to_accepted` clears a natural key's
    `rejected` state when it is re-offered and accepted — the mock's own decision tracking
    must not be add-only. A key capped once, then later re-offered under a fresh seq and
    accepted, must stop reporting `capped` on a later replay of the ORIGINAL seq too, not
    report it forever."""
    chunk_id = _seed(client)
    huge_turns = [_turn_with_text("x" * (_TRANSCRIPT_RECORD_MAX_BYTES + 1000))]

    first = client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1, turns=huge_turns)]},
    )
    assert first.json()["capped"] == [1]

    # The SAME natural key (segment_id="sg_1", turn_range_start=0), re-offered under a
    # fresh seq, now in-cap and accepted.
    reoffer = client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=2)]},
    )
    assert reoffer.json()["applied"] == [2]
    assert reoffer.json()["capped"] == []

    # A lost-ack replay of the ORIGINAL (now behind-the-mark) seq must no longer report
    # `capped` — the key's own decision moved to accepted, not stuck at its first outcome.
    replay = client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1, turns=huge_turns)]},
    )
    assert replay.json()["capped"] == []
    assert replay.json()["already_applied"] == [1]


def _turns_of_size(target_bytes: int) -> list[dict]:
    """A single turn whose ``turns`` JSON encodes to exactly ``target_bytes`` — the exact
    quantity the mock's own chunk-budget accounting sums (``len(json.dumps(record.get(
    "turns", [])).encode("utf-8"))``)."""
    overhead = len(json.dumps([_turn_with_text("")]).encode("utf-8"))
    return [_turn_with_text("x" * (target_bytes - overhead))]


def test_transcripts_reapplying_an_accepted_key_under_a_fresh_seq_does_not_recredit_the_chunk_budget(
    client: TestClient,
) -> None:
    """review round 6 F10: the real hub's ``TranscriptIngestService._apply`` returns early,
    with no re-crediting, once a natural key is already ``"accepted"`` — a re-offer under a
    fresh seq must not double-count its bytes against the chunk budget. 31 records at
    exactly 2 MiB each leave exactly 2 MiB of the 64 MiB budget free; re-offering an
    already-accepted key under a new seq must not consume that headroom, or the following
    genuinely new 2 MiB record — which fits exactly — gets wrongly capped."""
    chunk_id = _seed(client)
    two_mib = 2 * 1024 * 1024
    seq = 0
    for turn_range_start in range(31):
        seq += 1
        ack = client.post(
            "/api/fleet/transcripts",
            json={
                "runner_id": "r1",
                "records": [
                    _transcript_record(
                        chunk_id,
                        seq=seq,
                        turn_range_start=turn_range_start,
                        turn_range_end=turn_range_start,
                        turns=_turns_of_size(two_mib),
                    )
                ],
            },
        )
        assert ack.json()["applied"] == [seq], ack.text

    # Re-offer the FIRST accepted key (turn_range_start=0) under a fresh seq — must apply
    # with no re-adjudication and no re-crediting.
    seq += 1
    replay = client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [
                _transcript_record(
                    chunk_id, seq=seq, turn_range_start=0, turn_range_end=0, turns=_turns_of_size(two_mib)
                )
            ],
        },
    )
    assert replay.json()["applied"] == [seq]

    # The remaining 2 MiB of headroom is still free — a genuinely new key fits exactly,
    # which it would not if the replay above had re-credited its own 2 MiB a second time.
    seq += 1
    fresh = client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [
                _transcript_record(
                    chunk_id, seq=seq, turn_range_start=31, turn_range_end=31, turns=_turns_of_size(two_mib)
                )
            ],
        },
    )
    assert fresh.json()["applied"] == [seq]
    assert fresh.json()["capped"] == []


# --- lease-transcript read (blizzard#249) --------------------------------------


def test_lease_transcript_read_serves_a_leases_retained_segments(client: TestClient) -> None:
    chunk_id = _seed(client)
    ack = client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [_transcript_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)],
        },
    )
    assert ack.status_code == 200, ack.text

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 1})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["chunk_id"] == chunk_id
    assert body["node_id"] == "build"
    assert body["epoch"] == 1
    assert [t["text"] for t in body["turns"]] == ["hi"]


def test_lease_transcript_read_spans_every_ingested_record_in_seq_order(client: TestClient) -> None:
    """Submitted **reversed** relative to seq, so a pass actually pins 'sorts by seq'
    rather than merely 'preserves arrival order' (indistinguishable on an already-sorted
    input)."""
    chunk_id = _seed(client)
    first = _transcript_record(chunk_id, seq=1, turn_range_start=0, turn_range_end=0)
    second = _transcript_record(chunk_id, seq=2, turn_range_start=1, turn_range_end=1)
    second["turns"] = [_turn_with_text("second", index=1)]
    second["final"] = True
    ack = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [second, first]})
    assert ack.status_code == 200, ack.text

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 1})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [t["text"] for t in body["turns"]] == ["hi", "second"]
    # Renumbered across the whole read, like the real route: a segment's own index is
    # generation-local, so a lease's concatenation would otherwise restart it mid-list.
    assert [t["index"] for t in body["turns"]] == [0, 1]
    assert body["truncated"] is False


def test_lease_transcript_read_does_not_cross_node_ids(client: TestClient) -> None:
    """An epoch-crossing case already existed; a node_id-crossing one didn't."""
    chunk_id = _seed(client)
    client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1, node_id="build")]},
    )

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "review", "epoch": 1})

    assert resp.status_code == 200, resp.text
    assert resp.json()["turns"] == []


def test_lease_transcript_read_does_not_cross_chunk_ids(client: TestClient) -> None:
    """Nor a chunk_id-crossing one."""
    chunk_id = _seed(client)
    other_chunk_id = _seed(client)
    client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1)]},
    )

    resp = client.get(
        f"/api/fleet/chunks/{other_chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 1}
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["turns"] == []


def test_a_record_re_offered_under_a_fresh_seq_replaces_not_duplicates(client: TestClient) -> None:
    """The real hub dedupes on the natural key ``(segment_id, turn_range_start)``
    regardless of the offered ``seq`` (D8) — a re-offer (e.g. after an ack the runner
    missed) must not double the retained turns."""
    chunk_id = _seed(client)
    client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1, turn_range_start=0)]},
    )
    client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=99, turn_range_start=0)]},
    )

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 1})

    assert resp.status_code == 200, resp.text
    assert [t["text"] for t in resp.json()["turns"]] == ["hi"]


def test_lease_transcript_read_orders_by_spawn_generation_segment_id_turn_range_not_arrival(
    client: TestClient,
) -> None:
    """The real store's ``records_for_lease`` orders by ``(spawn_generation, segment_id,
    turn_range_start)`` — not by ingest/seq order, which a fixture whose seq happens to
    rise alongside its natural key cannot distinguish from a plain-arrival-order
    read-back. Two segments arrive in an order where seq and segment_id diverge: ``sg_b``
    first (lower seq), ``sg_a`` second (higher seq) — the read-back must still put
    ``sg_a`` first, matching the real store's key, not the arrival order."""
    chunk_id = _seed(client)
    client.post(
        "/api/fleet/transcripts",
        json={
            "runner_id": "r1",
            "records": [_transcript_record(chunk_id, seq=1, segment_id="sg_b", turn_range_start=0)],
        },
    )
    second = _transcript_record(chunk_id, seq=2, segment_id="sg_a", turn_range_start=0)
    second["turns"] = [_turn_with_text("from-sg-a")]
    ack = client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [second]})
    assert ack.status_code == 200, ack.text

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 1})

    assert resp.status_code == 200, resp.text
    assert [t["text"] for t in resp.json()["turns"]] == ["from-sg-a", "hi"]


def test_lease_transcript_read_is_empty_for_a_lease_with_no_retained_segments(client: TestClient) -> None:
    chunk_id = _seed(client)

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 1})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["turns"] == []


def test_lease_transcript_read_does_not_cross_epochs(client: TestClient) -> None:
    chunk_id = _seed(client)
    client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_transcript_record(chunk_id, seq=1)]},
    )

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/transcript-segments", params={"node_id": "build", "epoch": 2})

    assert resp.status_code == 200, resp.text
    assert resp.json()["turns"] == []


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


# --- test-control stop route ---------------------------------------------------


def test_seed_stop_reports_stopped_with_no_route_and_the_graph_still_readable(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)

    resp = client.post("/_seed/stop", json={"chunk_id": chunk_id})
    assert resp.status_code == 200
    assert resp.json() == {"stopped": True, "chunk_id": chunk_id}

    detail = client.get(f"/api/fleet/chunks/{chunk_id}").json()
    assert detail["status"] == "stopped"
    assert detail["route"] is None
    assert detail["current_node_id"] == "build"
    assert detail["work_refs"] == [{"source": "o-r", "ref": "1"}]


def test_seed_stop_404s_on_unknown_chunk(client: TestClient) -> None:
    resp = client.post("/_seed/stop", json={"chunk_id": "unknown"})
    assert resp.status_code == 404


def test_seed_stop_leaves_levers_unchanged(client: TestClient) -> None:
    chunk_id = _seed(client)
    _claim_and_fence(client, chunk_id)
    before = client.get("/_levers").json()

    client.post("/_seed/stop", json={"chunk_id": chunk_id})

    assert client.get("/_levers").json() == before


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
            "session_harnesses": ["claude", "codex"],
            "session_compaction_window": "100000",
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
    assert node["session_harnesses"] == ["claude", "codex"]
    assert node["session_compaction_window"] == "100000"
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
    assert node["session_harnesses"] == []
    assert node["session_compaction_window"] is None
    assert node["session_rotate"] is None


# --- graph-scoped artifacts --------------------------------------------------


def test_seeded_graph_artifacts_ride_the_claim_envelope(client: TestClient) -> None:
    spec = dict(_SPEC)
    spec["graph_artifacts"] = [
        {"name": "runbook", "kind": "asset", "content": "how to ship it"},
        {"name": "docket", "content": "the work item text"},
    ]
    resp = client.post("/_seed/chunk", json=spec)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]

    claim = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    # Authored order, so a silent sort flips these; "runbook" seeds its kind explicitly
    # while "docket" leans on the default, so both spellings have to reach the envelope.
    assert claim.json()["envelope"]["graph_artifacts"] == [
        {"name": "runbook", "kind": "asset", "content": "how to ship it"},
        {"name": "docket", "kind": "asset", "content": "the work item text"},
    ]


# --- system artifacts (ArtifactScope.SYSTEM, global) -------------------------


def test_system_artifacts_start_empty(client: TestClient) -> None:
    resp = client.get("/api/fleet/system-artifacts")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_seeded_system_artifact_is_readable_by_list_and_by_name(client: TestClient) -> None:
    seeded = client.post("/_seed/system-artifacts", json={"name": "garden/finding-format", "content": "the format"})
    assert seeded.status_code == 201, seeded.text
    assert seeded.json() == {"name": "garden/finding-format"}

    listed = client.get("/api/fleet/system-artifacts")
    assert listed.status_code == 200, listed.text
    assert listed.json() == [{"name": "garden/finding-format", "content": "the format"}]

    fetched = client.get("/api/fleet/system-artifacts/garden/finding-format")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json() == {"name": "garden/finding-format", "content": "the format"}


def test_get_system_artifact_404s_for_an_unpublished_name(client: TestClient) -> None:
    resp = client.get("/api/fleet/system-artifacts/ghost")
    assert resp.status_code == 404
    assert "ghost" in resp.json()["detail"]


def test_seeding_the_same_name_twice_replaces_it(client: TestClient) -> None:
    client.post("/_seed/system-artifacts", json={"name": "docket", "content": "first"})
    client.post("/_seed/system-artifacts", json={"name": "docket", "content": "second"})

    resp = client.get("/api/fleet/system-artifacts/docket")
    assert resp.json()["content"] == "second"


def test_reset_clears_seeded_system_artifacts(client: TestClient) -> None:
    client.post("/_seed/system-artifacts", json={"name": "docket", "content": "text"})
    assert client.post("/_seed/reset").status_code == 200

    resp = client.get("/api/fleet/system-artifacts")
    assert resp.json() == []


# --- scopes (global vocabulary, blizzard#582 D2) ------------------------------


def test_scopes_start_empty(client: TestClient) -> None:
    resp = client.get("/api/fleet/scopes")
    assert resp.status_code == 200, resp.text
    assert resp.json() == []


def test_seeded_scope_is_readable_by_the_fleet_route(client: TestClient) -> None:
    seeded = client.post("/_seed/scopes", json={"slug": "blizzard", "description": "the platform"})
    assert seeded.status_code == 201, seeded.text
    assert seeded.json() == {"slug": "blizzard"}

    listed = client.get("/api/fleet/scopes")
    assert listed.status_code == 200, listed.text
    assert len(listed.json()) == 1
    row = listed.json()[0]
    assert row["slug"] == "blizzard"
    assert row["description"] == "the platform"
    assert row["retired"] is False


def test_a_retired_scope_is_seedable_directly(client: TestClient) -> None:
    client.post("/_seed/scopes", json={"slug": "gone", "retired": True})

    resp = client.get("/api/fleet/scopes")
    assert resp.json()[0]["retired"] is True


def test_seeding_the_same_slug_twice_replaces_it(client: TestClient) -> None:
    client.post("/_seed/scopes", json={"slug": "blizzard", "description": "first"})
    client.post("/_seed/scopes", json={"slug": "blizzard", "description": "second"})

    resp = client.get("/api/fleet/scopes")
    assert len(resp.json()) == 1
    assert resp.json()[0]["description"] == "second"


def test_reset_clears_seeded_scopes(client: TestClient) -> None:
    client.post("/_seed/scopes", json={"slug": "blizzard"})
    assert client.post("/_seed/reset").status_code == 200

    resp = client.get("/api/fleet/scopes")
    assert resp.json() == []


# --- garden findings (worker-scoped bucket read) ------------------------------


def _garden_spec(*, garden_run: dict | None, garden_findings: list[dict] | None = None) -> dict:
    spec = dict(_SPEC)
    if garden_run is not None:
        spec["garden_run"] = garden_run
    if garden_findings is not None:
        spec["garden_findings"] = garden_findings
    return spec


def test_garden_findings_reads_the_seeded_live_bucket(client: TestClient) -> None:
    spec = _garden_spec(
        garden_run={"routine_name": "nightly", "scope_slug": "blizzard"},
        garden_findings=[
            {"finding_id": "fin_1", "class": "stale-docstring", "locus": "a.py:1", "summary": "s"},
        ],
    )
    resp = client.post("/_seed/chunk", json=spec)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]

    findings = client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings")
    assert findings.status_code == 200, findings.text
    assert findings.json() == [
        {
            "finding_id": "fin_1",
            "routine_name": "nightly",
            "scope_slug": "blizzard",
            "class": "stale-docstring",
            "locus": "a.py:1",
            "summary": "s",
            "introduced": None,
            "introduced_at": None,
            "first_observed_at": None,
            "live": True,
            "state": "live",
            "note": None,
            "last_seen_at": None,
            "observed_count": 0,
            "source": "routine",
            "severity": None,
            "raised_by_chunk_id": None,
        }
    ]


def test_garden_findings_excludes_a_seeded_non_live_finding(client: TestClient) -> None:
    spec = _garden_spec(
        garden_run={"routine_name": "nightly", "scope_slug": "blizzard"},
        garden_findings=[
            {"finding_id": "fin_1", "class": "c", "locus": "a.py:1", "summary": "s"},
            {
                "finding_id": "fin_2",
                "class": "c",
                "locus": "a.py:2",
                "summary": "s",
                "live": False,
                "state": "resolved",
            },
        ],
    )
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    findings = client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings")
    assert [row["finding_id"] for row in findings.json()] == ["fin_1"]


def test_garden_findings_404s_on_a_chunk_with_no_run_context(client: TestClient) -> None:
    chunk_id = _seed(client)  # the plain `_SPEC` chunk seeds no `garden_run` at all

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings")
    assert resp.status_code == 404
    assert "no run context" in resp.json()["detail"]


def test_garden_findings_404s_on_an_unknown_chunk(client: TestClient) -> None:
    resp = client.get("/api/fleet/chunks/ch_ghost/garden/findings")
    assert resp.status_code == 404
    assert "ch_ghost" in resp.json()["detail"]


# --- garden proposals (worker-scoped open-bucket read) ------------------------


def _proposal_spec(*, garden_run: dict | None, garden_proposals: list[dict] | None = None) -> dict:
    spec = dict(_SPEC)
    if garden_run is not None:
        spec["garden_run"] = garden_run
    if garden_proposals is not None:
        spec["garden_proposals"] = garden_proposals
    return spec


def test_garden_proposals_reads_the_seeded_open_bucket(client: TestClient) -> None:
    spec = _proposal_spec(
        garden_run={"routine_name": "nightly", "scope_slug": "blizzard"},
        garden_proposals=[
            {"proposal_id": "prop_1", "class": "mechanize", "title": "t", "body": "b", "findings": ["fin_1"]},
        ],
    )
    resp = client.post("/_seed/chunk", json=spec)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]

    proposals = client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals")
    assert proposals.status_code == 200, proposals.text
    assert proposals.json() == [
        {
            "proposal_id": "prop_1",
            "routine_name": "nightly",
            "class": "mechanize",
            "title": "t",
            "body": "b",
            "findings": ["fin_1"],
            "created_at": "2026-07-13T00:00:00+00:00",
            "closure": None,
        }
    ]


def test_garden_proposals_state_closed_returns_only_closed_ones_with_their_closure(client: TestClient) -> None:
    spec = _proposal_spec(
        garden_run={"routine_name": "nightly", "scope_slug": "blizzard"},
        garden_proposals=[
            {"proposal_id": "prop_open", "class": "mechanize", "title": "t", "body": "b", "findings": ["fin_1"]},
            {
                "proposal_id": "prop_closed",
                "class": "mechanize",
                "title": "t",
                "body": "b",
                "findings": ["fin_1"],
                "closure": {"closure": "passed", "reason": "not worth it"},
            },
        ],
    )
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    proposals = client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals", params={"state": "closed"})
    assert proposals.status_code == 200, proposals.text
    body = proposals.json()
    assert [row["proposal_id"] for row in body] == ["prop_closed"]
    assert body[0]["closure"] == {
        "closure": "passed",
        "reason": "not worth it",
        "closed_by": "u_1",
        "closed_at": "2026-07-13T00:00:00+00:00",
        "item_outcome": None,
        "source": None,
        "ref": None,
    }


def test_garden_proposals_state_all_returns_open_and_closed(client: TestClient) -> None:
    spec = _proposal_spec(
        garden_run={"routine_name": "nightly", "scope_slug": "blizzard"},
        garden_proposals=[
            {"proposal_id": "prop_open", "class": "mechanize", "title": "t", "body": "b", "findings": ["fin_1"]},
            {
                "proposal_id": "prop_closed",
                "class": "mechanize",
                "title": "t",
                "body": "b",
                "findings": ["fin_1"],
                "closure": {"closure": "passed", "reason": "not worth it"},
            },
        ],
    )
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    proposals = client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals", params={"state": "all"})
    assert proposals.status_code == 200, proposals.text
    assert [row["proposal_id"] for row in proposals.json()] == ["prop_open", "prop_closed"]


def test_garden_proposals_404s_on_a_chunk_with_no_run_context(client: TestClient) -> None:
    chunk_id = _seed(client)  # the plain `_SPEC` chunk seeds no `garden_run` at all

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/garden/proposals")
    assert resp.status_code == 404
    assert "no run context" in resp.json()["detail"]


def test_garden_proposals_404s_on_an_unknown_chunk(client: TestClient) -> None:
    resp = client.get("/api/fleet/chunks/ch_ghost/garden/proposals")
    assert resp.status_code == 404
    assert "ch_ghost" in resp.json()["detail"]


# --- fleet analytics (worker-scoped counts/spend read, blizzard#545) ----------

_ANALYTICS_ROUTES = [
    "counts/files",
    "counts/skills",
    "counts/agent-types",
    "counts/nodes",
    "spend/nodes",
    "spend/graphs",
]


def _analytics_spec(*, garden_run: dict | None, analytics: dict | None = None) -> dict:
    spec = dict(_SPEC)
    if garden_run is not None:
        spec["garden_run"] = garden_run
    if analytics is not None:
        spec["analytics"] = analytics
    return spec


@pytest.mark.parametrize("suffix", _ANALYTICS_ROUTES)
def test_analytics_404s_on_a_chunk_with_no_run_context(client: TestClient, suffix: str) -> None:
    chunk_id = _seed(client)  # the plain `_SPEC` chunk seeds no `garden_run` at all

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/analytics/{suffix}", params={"since": "2020-01-01T00:00:00Z"})
    assert resp.status_code == 404
    assert "no run context" in resp.json()["detail"]


@pytest.mark.parametrize("suffix", _ANALYTICS_ROUTES)
def test_analytics_404s_on_an_unknown_chunk(client: TestClient, suffix: str) -> None:
    resp = client.get(f"/api/fleet/chunks/ch_ghost/analytics/{suffix}", params={"since": "2020-01-01T00:00:00Z"})
    assert resp.status_code == 404
    assert "ch_ghost" in resp.json()["detail"]


@pytest.mark.parametrize("suffix", _ANALYTICS_ROUTES)
def test_analytics_422s_without_since(client: TestClient, suffix: str) -> None:
    spec = _analytics_spec(garden_run={"routine_name": "nightly", "scope_slug": "blizzard"})
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/analytics/{suffix}")
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("suffix", _ANALYTICS_ROUTES)
def test_analytics_serves_the_seeded_rows_whatever_the_window(client: TestClient, suffix: str) -> None:
    """The mock does not aggregate (blizzard#545 D5): the seeded row is served as-is,
    regardless of the window named — proven here by naming only the required `since`,
    ignored like `until` would be."""
    key = (
        f"counts_{suffix.split('/')[1].replace('-', '_')}"
        if suffix.startswith("counts/")
        else ("spend_nodes" if suffix == "spend/nodes" else "spend_graphs")
    )
    row = (
        {"key": "k1", "count": 3}
        if suffix.startswith("counts/")
        else {
            "key": "k1",
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_create_tokens": 5,
            "cost_usd": 0.1,
            "cost_partial": False,
        }
    )
    spec = _analytics_spec(garden_run={"routine_name": "nightly", "scope_slug": "blizzard"}, analytics={key: [row]})
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/analytics/{suffix}", params={"since": "2020-01-01T00:00:00Z"})
    assert resp.status_code == 200, resp.text
    envelope_key = "counts" if suffix.startswith("counts/") else "spend"
    assert resp.json()[envelope_key] == [row]


# --- answered findings (worker-scoped per-chunk read) -------------------------


def _answered_spec(*, garden_answered_findings: list[dict] | None) -> dict:
    spec = dict(_SPEC)
    if garden_answered_findings is not None:
        spec["garden_answered_findings"] = garden_answered_findings
    return spec


def test_chunk_findings_reads_the_seeded_answered_set(client: TestClient) -> None:
    spec = _answered_spec(
        garden_answered_findings=[
            {
                "finding_id": "fin_1",
                "routine_name": "nightly",
                "scope_slug": "blizzard",
                "class": "stale-docstring",
                "locus": "a.py:1",
                "summary": "s",
            },
        ]
    )
    resp = client.post("/_seed/chunk", json=spec)
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]

    findings = client.get(f"/api/fleet/chunks/{chunk_id}/findings")
    assert findings.status_code == 200, findings.text
    assert findings.json() == [
        {
            "finding_id": "fin_1",
            "routine_name": "nightly",
            "scope_slug": "blizzard",
            "class": "stale-docstring",
            "locus": "a.py:1",
            "summary": "s",
            "introduced": None,
            "introduced_at": None,
            "first_observed_at": None,
            "live": True,
            "state": "live",
            "note": None,
            "last_seen_at": None,
            "observed_count": 0,
            "source": "routine",
            "severity": None,
            "raised_by_chunk_id": None,
        }
    ]


def test_chunk_findings_404s_on_a_chunk_with_no_answered_set(client: TestClient) -> None:
    chunk_id = _seed(client)  # the plain `_SPEC` chunk seeds no `garden_answered_findings` at all

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/findings")
    assert resp.status_code == 404
    assert "no accepted, minted garden proposal" in resp.json()["detail"]


def test_chunk_findings_404s_on_an_unknown_chunk(client: TestClient) -> None:
    resp = client.get("/api/fleet/chunks/ch_ghost/findings")
    assert resp.status_code == 404
    assert "ch_ghost" in resp.json()["detail"]


def test_chunk_finding_reads_one_finding_within_the_answered_set(client: TestClient) -> None:
    spec = _answered_spec(
        garden_answered_findings=[
            {
                "finding_id": "fin_1",
                "routine_name": "nightly",
                "scope_slug": "blizzard",
                "class": "c",
                "locus": "a.py:1",
                "summary": "s",
            },
            {
                "finding_id": "fin_2",
                "routine_name": "nightly",
                "scope_slug": "blizzard",
                "class": "c",
                "locus": "a.py:2",
                "summary": "s",
            },
        ]
    )
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_2")
    assert resp.status_code == 200, resp.text
    assert resp.json()["finding_id"] == "fin_2"


def test_chunk_finding_404s_on_an_id_outside_the_answered_set(client: TestClient) -> None:
    spec = _answered_spec(
        garden_answered_findings=[
            {
                "finding_id": "fin_1",
                "routine_name": "nightly",
                "scope_slug": "blizzard",
                "class": "c",
                "locus": "a.py:1",
                "summary": "s",
            },
        ]
    )
    chunk_id = client.post("/_seed/chunk", json=spec).json()["chunk_id"]

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_other")
    assert resp.status_code == 404
    assert "not among the findings" in resp.json()["detail"]


def test_chunk_finding_404s_on_a_chunk_with_no_answered_set(client: TestClient) -> None:
    chunk_id = _seed(client)

    resp = client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_1")
    assert resp.status_code == 404
    assert "no accepted, minted garden proposal" in resp.json()["detail"]
