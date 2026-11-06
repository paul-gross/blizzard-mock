"""Problem and legacy dependency and resource responses over the mock HTTP boundary."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app


def test_foreign_question_poll_matches_unknown_and_preserves_answer() -> None:
    client = TestClient(create_app())
    owner = client.post("/api/runners", json={"name": "owner"}).json()
    tenant = client.post("/_seed/tenants", json={"name": "other"}).json()["tenant_id"]
    foreign = client.post("/api/runners", headers={"X-Blizzard-Tenant": tenant}, json={"name": "other"}).json()
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    foreign_headers = {"Authorization": f"Bearer {foreign['token']}"}
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    facts = [
        {
            "seq": 1,
            "kind": "question.asked",
            "payload": {"question_id": "q1", "chunk_id": chunk, "epoch": 1, "question": "secret"},
        }
    ]
    assert client.post("/api/fleet/events", headers=owner_headers, json={"facts": facts}).json()["applied"] == [1]
    assert client.post("/_seed/answer", json={"question_id": "q1", "answer": "private"}).status_code == 200
    path = "/api/fleet/questions/q1"
    before = client.get(path, headers=owner_headers).json()
    assert before["answer"] == "private"
    modern = client.get(path, headers=foreign_headers)
    assert modern.status_code == 404
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:question-not-found",
        "title": "Question not found",
        "detail": "unknown question q1",
        "instance": path,
        "status": 404,
    }
    assert client.get("/api/fleet/questions/missing", headers=foreign_headers).json()["type"] == modern.json()["type"]
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    assert client.get(path, headers=owner_headers).status_code == 200
    legacy = client.get(path, headers=foreign_headers)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": "unknown question q1"}
    assert client.get(path, headers=foreign_headers).json() == modern.json()
    assert client.get(path, headers=owner_headers).json() == before


@pytest.mark.parametrize(
    ("suffix", "body", "lever", "kind"),
    [
        ("pause", {}, "legacy_read_refusals", "chunk-not-found"),
        ("resume", {}, "legacy_read_refusals", "chunk-not-found"),
        ("release", {"epoch": 1, "reason": "test"}, "legacy_read_refusals", "chunk-not-found"),
        ("completions", {"choice": "x", "epoch": 1, "from_node_id": "x"}, "legacy_read_refusals", "chunk-not-found"),
        ("decisions", {"epoch": 1, "from_node_id": "x"}, "legacy_read_refusals", "chunk-not-found"),
        ("hub-advance", {}, "legacy_read_refusals", "chunk-not-found"),
        ("artifact-check", {"name": "delta", "content": "x"}, "legacy_read_refusals", "chunk-not-found"),
        ("route-token", {}, "legacy_rekey_refusals", "route-not-found"),
        ("routes", {"workspace_id": "w"}, "legacy_claim_refusals", "chunk-not-found"),
    ],
)
def test_foreign_chunk_mutations_match_unknown(suffix: str, body: dict[str, object], lever: str, kind: str) -> None:
    app = create_app()
    client = TestClient(app)
    tenant = client.post("/_seed/tenants", json={"name": "other"}).json()["tenant_id"]
    foreign = client.post("/api/runners", headers={"X-Blizzard-Tenant": tenant}, json={"name": "other"}).json()
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    headers = {"Authorization": f"Bearer {foreign['token']}"}
    path = "/api/fleet/routes" if suffix == "routes" else f"/api/fleet/chunks/{chunk}/{suffix}"
    missing_path = path.replace(chunk, "missing")
    payload = {**body, "chunk_id": chunk} if suffix == "routes" else body
    missing_payload = {**body, "chunk_id": "missing"} if suffix == "routes" else body
    before = app.state.service._state.get_chunk(chunk).model_dump()
    modern = client.post(path, headers=headers, json=payload)
    missing = client.post(missing_path, headers=headers, json=missing_payload)
    assert modern.status_code == missing.status_code == 404
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json()["type"] == missing.json()["type"] == f"urn:blizzard:problem:{kind}"
    detail = f"chunk {chunk} has no live route" if suffix == "route-token" else f"unknown chunk {chunk}"
    assert modern.json()["detail"] == detail
    assert modern.json()["instance"] == path
    client.post(f"/_levers/{lever}", json={"chunk_id": chunk, "remaining": 1})
    legacy = client.post(path, headers=headers, json=payload)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": detail}
    assert client.post(path, headers=headers, json=payload).json() == modern.json()
    assert app.state.service._state.get_chunk(chunk).model_dump() == before


@pytest.mark.parametrize(
    "suffix",
    [
        "envelope",
        "work-items",
        "pm-items",
        "garden/findings",
        "garden/proposals",
        "garden/proposals/page",
        "analytics/counts/files",
        "analytics/counts/skills",
        "analytics/counts/agent-types",
        "analytics/counts/nodes",
        "analytics/spend/nodes",
        "analytics/spend/graphs",
        "findings",
        "findings/example",
        "scopes",
    ],
)
def test_foreign_chunk_reads_match_unknown_in_both_modes(suffix: str) -> None:
    client = TestClient(create_app())
    tenant = client.post("/_seed/tenants", json={"name": "other"}).json()["tenant_id"]
    foreign = client.post("/api/runners", headers={"X-Blizzard-Tenant": tenant}, json={"name": "other"}).json()
    owner = client.post(
        "/api/runners", headers={"X-Blizzard-Tenant": "ten_00000000000000000000000000"}, json={"name": "owner"}
    ).json()
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    headers = {"Authorization": f"Bearer {foreign['token']}"}
    owner_headers = {"Authorization": f"Bearer {owner['token']}"}
    path = "/api/fleet/scopes" if suffix == "scopes" else f"/api/fleet/chunks/{chunk}/{suffix}"
    params = {"chunk_id": chunk} if suffix == "scopes" else {"since": "2025-01-01T00:00:00Z"}
    missing_path = path.replace(chunk, "missing")
    missing_params = {"chunk_id": "missing"} if suffix == "scopes" else params
    same_tenant = client.get(path, headers=owner_headers, params=params)
    if suffix.startswith(("garden/", "analytics/", "findings")):
        assert same_tenant.json()["type"] in {
            "urn:blizzard:problem:no-run-context",
            "urn:blizzard:problem:no-answered-proposal",
        }
    else:
        assert same_tenant.status_code in (200, 409)
    modern = client.get(path, headers=headers, params=params)
    assert modern.status_code == 404
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:chunk-not-found",
        "title": "Chunk not found",
        "detail": f"unknown chunk {chunk}",
        "instance": path,
        "status": 404,
    }
    missing = client.get(missing_path, headers=headers, params=missing_params)
    assert missing.json()["type"] == modern.json()["type"]
    client.post("/_levers/legacy_read_refusals", json={"chunk_id": chunk, "remaining": 1})
    legacy = client.get(path, headers=headers, params=params)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": f"unknown chunk {chunk}"}
    assert client.get(path, headers=headers, params=params).json() == modern.json()


def test_foreign_tenant_chunk_detail_is_unknown_in_both_modes() -> None:
    client = TestClient(create_app())
    tenant = client.post("/_seed/tenants", json={"name": "other"}).json()["tenant_id"]
    other = client.post("/api/runners", headers={"X-Blizzard-Tenant": tenant}, json={"name": "other"}).json()
    owner = client.post(
        "/api/runners", headers={"X-Blizzard-Tenant": "ten_00000000000000000000000000"}, json={"name": "owner"}
    ).json()
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    path = f"/api/fleet/chunks/{chunk}"
    headers = {"Authorization": f"Bearer {other['token']}"}
    assert client.get(path, headers={"Authorization": f"Bearer {owner['token']}"}).status_code == 200
    modern = client.get(path, headers=headers)
    assert modern.status_code == 404
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:chunk-not-found",
        "title": "Chunk not found",
        "detail": f"unknown chunk {chunk}",
        "instance": path,
        "status": 404,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.get(path, headers=headers)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": f"unknown chunk {chunk}"}
    assert client.get(path, headers=headers).json() == modern.json()


@pytest.mark.parametrize("case", ["missing", "wrong", "foreign", "no-route", "rekeyed"])
def test_enforced_release_token_refusal_preserves_state(case: str) -> None:
    app = create_app()
    client = TestClient(app)
    owner = client.post("/api/runners", json={"name": "owner"}).json()
    other = client.post("/api/runners", json={"name": "other"}).json()
    headers = {"Authorization": f"Bearer {owner['token']}"}
    client.post(
        "/api/fleet/runners",
        headers=headers,
        json={"workspace_id": "w", "capabilities": [{"harness_id": "claude_code", "default": True}]},
    )
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    token = None
    if case != "no-route":
        token = client.post("/api/fleet/routes", headers=headers, json={"chunk_id": chunk, "workspace_id": "w"}).json()[
            "route_token"
        ]
    if case == "rekeyed":
        client.post(f"/api/fleet/chunks/{chunk}/route-token", headers=headers)
    if case == "foreign":
        headers = {"Authorization": f"Bearer {other['token']}"}
    body: dict[str, object] = {"epoch": 999, "reason": "test"}
    if case in ("foreign", "rekeyed"):
        body["route_token"] = token
    elif case == "wrong":
        body["route_token"] = "wrong"
    client.post("/_levers/enforce_release_route_token", json={"chunk_id": chunk})
    path = f"/api/fleet/chunks/{chunk}/release"
    before = app.state.service._state.get_chunk(chunk).model_dump()
    if case == "no-route":
        detail = "chunk has no live route — nothing to authorize this write against"
    elif case == "foreign":
        detail = f"runner_id {other['runner_id']!r} does not hold the chunk's live route"
    else:
        detail = "route token missing or does not match the chunk's live route"
    modern = client.post(path, headers=headers, json=body)
    assert modern.status_code == 403
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:route-not-held",
        "title": "Route not held",
        "detail": f"chunk {chunk}: {detail}",
        "instance": path,
        "status": 403,
    }
    client.post("/_levers/legacy_read_refusals", json={"chunk_id": chunk, "remaining": 1})
    legacy = client.post(path, headers=headers, json=body)
    assert legacy.status_code == 403
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": modern.json()["detail"]}
    assert client.post(path, headers=headers, json=body).json() == modern.json()
    assert app.state.service._state.get_chunk(chunk).model_dump() == before


def test_rekey_unknown_chunk_has_no_live_route_in_both_modes() -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    path = "/api/fleet/chunks/missing/route-token"
    modern = client.post(path, headers=headers)
    assert modern.status_code == 404
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:route-not-found",
        "title": "Route not found",
        "detail": "chunk missing has no live route",
        "instance": path,
        "status": 404,
    }
    client.post("/_levers/legacy_rekey_refusals", json={"remaining": 1})
    legacy = client.post(path, headers=headers)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": "chunk missing has no live route"}
    assert client.post(path, headers=headers).json() == modern.json()


def test_heartbeat_ownership_missing_registration_and_liveness() -> None:
    clock = FixedClock(datetime(2026, 1, 1, tzinfo=UTC))
    client = TestClient(create_app(clock=clock))
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    headers = {"Authorization": f"Bearer {runner['token']}"}
    own = f"/api/fleet/runners/{runner['runner_id']}/heartbeats"
    foreign = "/api/fleet/runners/missing/heartbeats"

    response = client.post(foreign, headers=headers)
    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:blizzard:problem:runner-ownership",
        "title": "Runner ownership refused",
        "detail": f"token belongs to runner {runner['runner_id']!r}, not 'missing'",
        "instance": foreign,
        "status": 403,
    }
    missing = client.post(own, headers=headers)
    assert missing.status_code == 404
    assert missing.headers["content-type"] == "application/problem+json"
    assert missing.json() == {
        "type": "urn:blizzard:problem:runner-not-found",
        "title": "Runner not found",
        "detail": f"unknown runner {runner['runner_id']}",
        "instance": own,
        "status": 404,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy_missing = client.post(own, headers=headers)
    assert legacy_missing.status_code == 404
    assert legacy_missing.headers["content-type"] == "application/json"
    assert legacy_missing.json() == {"detail": missing.json()["detail"]}
    assert client.post(own, headers=headers).json() == missing.json()
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.post(foreign, headers=headers)
    assert legacy.status_code == 403
    assert legacy.json() == {"detail": response.json()["detail"]}
    assert client.post(foreign, headers=headers).json() == response.json()
    assert client.post(own).status_code == 401

    assert client.post("/api/fleet/runners", headers=headers, json={"workspace_id": "ws"}).status_code == 201
    clock.tick(60)
    beat = client.post(own, headers=headers)
    assert beat.status_code == 204
    assert beat.content == b""
    view = client.get(f"/api/fleet/runners/{runner['runner_id']}", headers=headers).json()
    assert view["last_seen_at"] == clock.now().isoformat()


def test_runner_decisions_ownership_and_legacy_expiry() -> None:
    client = TestClient(create_app())
    first = client.post("/api/runners", json={"name": "first"}).json()
    second = client.post("/api/runners", json={"name": "second"}).json()
    headers = {"Authorization": f"Bearer {first['token']}"}
    own_path = f"/api/fleet/runners/{first['runner_id']}/decisions"
    path = f"/api/fleet/runners/{second['runner_id']}/decisions"
    assert client.get(own_path, headers=headers).json() == {"decisions": []}
    modern = client.get(path, headers=headers)
    assert modern.status_code == 403
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:runner-ownership",
        "title": "Runner ownership refused",
        "detail": f"token belongs to runner {first['runner_id']!r}, not {second['runner_id']!r}",
        "instance": path,
        "status": 403,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    assert client.get(own_path, headers=headers).json() == {"decisions": []}
    legacy = client.get(path, headers=headers)
    assert legacy.status_code == 403
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": modern.json()["detail"]}
    assert client.get(path, headers=headers).json() == modern.json()
    assert client.get("/api/fleet/runners/missing/decisions", headers=headers).status_code == 403
    assert client.get(f"/api/fleet/runners/{second['runner_id']}", headers=headers).json()["title"] == (
        "Runner ownership refused"
    )
    assert client.get(own_path).status_code == 401


def test_queue_query_project_refusal_and_filter() -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    other = client.post("/_seed/projects", json={"slug": "other", "name": "Other"}).json()["project_id"]
    spec = {"entry": "b", "nodes": {"b": {"executor": "hub"}}}
    client.post("/_seed/chunk", json=spec)
    chunk = client.post("/_seed/chunk", json={**spec, "project_id": other}).json()["chunk_id"]
    path = "/api/fleet/queue/peek"
    entries = client.get(path, headers=headers, params={"project": "other"}).json()["entries"]
    assert [(e["chunk_id"], e["position"]) for e in entries] == [(chunk, 1)]
    assert client.get(path, headers=headers, params={"project": other}).json()["entries"] == entries
    response = client.get(path, headers=headers, params={"project": "missing"})
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:blizzard:problem:unknown-project",
        "title": "Unknown project",
        "detail": [{"loc": ["query", "project"], "msg": "unknown project 'missing'", "type": "unknown_project"}],
        "instance": path,
        "status": 422,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.get(path, headers=headers, params={"project": "missing"})
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": response.json()["detail"]}
    assert client.get(path, headers=headers, params={"project": "missing"}).json() == response.json()


def test_auth_and_tenant_refusals_and_legacy_expiry() -> None:
    client = TestClient(create_app())
    response = client.get("/api/fleet/queue/peek")
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "urn:blizzard:problem:authentication-required"
    client.post("/_levers/legacy_auth_refusals", json={"remaining": 1})
    legacy = client.get("/api/fleet/queue/peek")
    assert legacy.json() == {"detail": response.json()["detail"]}
    assert legacy.headers["content-type"] == "application/json"
    assert client.get("/api/fleet/queue/peek").json()["type"] == response.json()["type"]
    missing = client.post("/api/hosts", json={"name": "host"}, headers={"X-Blizzard-Tenant": "missing"})
    assert missing.status_code == 404
    assert missing.json()["type"] == "urn:blizzard:problem:tenant-not-found"
    assert missing.json()["detail"] == "no such tenant"
    client.post("/_seed/tenants", json={"name": "other"})
    required = client.post("/api/hosts", json={"name": "host"})
    assert required.status_code == 409
    assert required.json()["type"] == "urn:blizzard:problem:tenant-required"
    assert required.json()["detail"]["code"] == "tenant_required"


def test_envelope_unknown_modern_and_legacy() -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    path = "/api/fleet/chunks/missing/envelope"
    modern = client.get(path, headers=headers)
    assert modern.status_code == 404
    assert modern.json()["type"] == "urn:blizzard:problem:chunk-not-found"
    assert modern.json()["instance"] == path
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.get(path, headers=headers)
    assert legacy.json() == {"detail": modern.json()["detail"]}
    assert legacy.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    ("body", "status", "kind"),
    [
        ({"name": "existing"}, 409, "host-name-taken"),
        ({"name": "new", "adopt": ["missing"]}, 404, "runner-not-found"),
    ],
)
def test_host_bootstrap_refusals(body: dict[str, object], status: int, kind: str) -> None:
    client = TestClient(create_app())
    assert client.post("/api/hosts", json={"name": "existing"}).status_code == 201
    modern = client.post("/api/hosts", json=body)
    assert modern.status_code == status
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json()["type"] == f"urn:blizzard:problem:{kind}"
    assert modern.json()["instance"] == "/api/hosts"
    assert modern.json()["status"] == status
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.post("/api/hosts", json=body)
    assert legacy.status_code == status
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": modern.json()["detail"]}


@pytest.mark.parametrize("legacy", [False, True])
def test_host_bootstrap_rejects_machine_credentials_before_writing(legacy: bool) -> None:
    client = TestClient(create_app())
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    host = client.post("/api/hosts", json={"name": "existing"}).json()
    cases = (
        (
            runner["token"],
            "foreign",
            403,
            "runner-credential-forbidden",
            "Runner credential forbidden",
            f"runner token for {runner['runner_id']!r} is not valid on an operator verb",
        ),
        (
            host["token"],
            "foreign",
            400,
            "tenant-credential-mismatch",
            "Tenant credential mismatch",
            "X-Blizzard-Tenant names a tenant other than the presented credential's",
        ),
        (
            host["token"],
            None,
            403,
            "machine-credential-forbidden",
            "Machine credential forbidden",
            "a machine credential is not valid here",
        ),
    )
    for token, tenant, status, kind, title, detail in cases:
        if legacy:
            client.post("/_levers/legacy_auth_refusals", json={"remaining": 1})
        headers = {"Authorization": f"Bearer {token}"}
        if tenant:
            headers["X-Blizzard-Tenant"] = tenant
        response = client.post("/api/hosts", headers=headers, json={"name": "new"})
        assert response.status_code == status
        if legacy:
            assert response.headers["content-type"] == "application/json"
            assert response.json() == {"detail": detail}
        else:
            assert response.headers["content-type"] == "application/problem+json"
            assert response.json() == {
                "type": f"urn:blizzard:problem:{kind}",
                "title": title,
                "detail": detail,
                "instance": "/api/hosts",
                "status": status,
            }
    assert client.post("/api/hosts", json={"name": "new"}).status_code == 201


def test_runner_read_disappears_after_authentication() -> None:
    client = TestClient(create_app())
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    headers = {"Authorization": f"Bearer {runner['token']}"}
    path = f"/api/fleet/runners/{runner['runner_id']}"
    client.post("/_levers/runner_read_missing", json={"remaining": 2})
    foreign = client.get("/api/fleet/runners/other", headers=headers)
    assert foreign.status_code == 403
    response = client.get(path, headers=headers)
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:blizzard:problem:runner-not-found",
        "title": "Runner not found",
        "detail": f"unknown runner {runner['runner_id']}",
        "instance": path,
        "status": 404,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.get(path, headers=headers)
    assert legacy.status_code == 404
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": response.json()["detail"]}
    assert client.get(path, headers=headers).status_code == 409


def test_host_add_lifecycle_conflict_is_atomic_and_expires() -> None:
    client = TestClient(create_app())
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    runner_id = runner["runner_id"]
    body = {"name": "host", "adopt": [runner_id]}
    client.post(
        "/_levers/host_add_runner_lifecycle_conflict", json={"remaining": 2, "payload": {"runner_id": runner_id}}
    )
    assert client.post("/api/hosts", json={"name": "other", "adopt": ["missing"]}).status_code == 404
    response = client.post("/api/hosts", json=body)
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:blizzard:problem:runner-lifecycle-conflict",
        "title": "Runner lifecycle conflict",
        "detail": f"runner {runner_id} changed after it was read — run it again",
        "instance": "/api/hosts",
        "status": 409,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.post("/api/hosts", json=body)
    assert legacy.status_code == 409
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": response.json()["detail"]}
    success = client.post("/api/hosts", json=body)
    assert success.status_code == 201
    assert success.json()["adopted"] == [runner_id]


def test_host_add_retired_adoption_precedes_hosted_and_name_conflicts() -> None:
    client = TestClient(create_app())
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    held = client.post("/api/hosts", json={"name": "held", "adopt": [runner["runner_id"]]}).json()
    client.post(f"/_seed/runners/{runner['runner_id']}/retire", json={"by": "operator"})
    body = {"name": "held", "adopt": [runner["runner_id"]]}
    response = client.post("/api/hosts", json=body)
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:blizzard:problem:runner-retired",
        "title": "Runner retired",
        "detail": f"runner {runner['runner_id']} is retired — adoption refused; `reinstate` it first",
        "instance": "/api/hosts",
        "status": 409,
    }
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.post("/api/hosts", json=body)
    assert legacy.status_code == 409
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": response.json()["detail"]}
    assert (
        client.post("/api/hosts", json={"name": "new", "adopt": [runner["runner_id"]]}).json()["type"]
        == response.json()["type"]
    )
    host_headers = {"Authorization": f"Bearer {held['token']}"}
    assert client.get("/api/fleet/host", headers=host_headers).json()["runners"] == []
    assert client.post("/api/hosts", json={"name": "new"}).status_code == 201


def test_host_add_already_hosted_refusal_names_held_host() -> None:
    client = TestClient(create_app())
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    host = client.post("/api/hosts", json={"name": "held", "adopt": [runner["runner_id"]]}).json()
    response = client.post("/api/hosts", json={"name": "new", "adopt": [runner["runner_id"]]})
    assert response.status_code == 409
    assert response.json()["type"] == "urn:blizzard:problem:runner-already-hosted"
    assert response.json()["detail"] == f"runner {runner['runner_id']} already stands under host {host['host_id']}"


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("completions", {"choice": "x", "epoch": 1, "from_node_id": "x"}),
        ("decisions", {"epoch": 1, "from_node_id": "x"}),
        ("hub-advance", {}),
        ("artifact-check", {"name": "delta", "content": "x"}),
    ],
)
def test_missing_chunk_mutations_modern_and_legacy(suffix: str, body: dict[str, object]) -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    path = f"/api/fleet/chunks/missing/{suffix}"
    modern = client.post(path, headers=headers, json=body)
    assert modern.status_code == 404
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json()["type"] == "urn:blizzard:problem:chunk-not-found"
    client.post("/_levers/legacy_read_refusals", json={"remaining": 1})
    legacy = client.post(path, headers=headers, json=body)
    assert legacy.json() == {"detail": modern.json()["detail"]}
    assert legacy.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    ("suffix", "body"),
    [
        ("envelope", None),
        ("completions", {"choice": "x", "epoch": 1, "from_node_id": "x"}),
        ("decisions", {"epoch": 1, "from_node_id": "x"}),
        ("hub-advance", {}),
        ("artifact-check", {"name": "delta", "content": "x"}),
    ],
)
def test_missing_pinned_graph_on_runner_calls(suffix: str, body: dict[str, object] | None) -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "hub"}}}).json()["chunk_id"]
    before = client.get(f"/api/fleet/chunks/{chunk}", headers=headers).json()
    client.post("/_levers/pinned_graph_missing", json={"chunk_id": chunk, "remaining": 3})
    path = f"/api/fleet/chunks/{chunk}/{suffix}"
    response = client.get(path, headers=headers) if body is None else client.post(path, headers=headers, json=body)
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json() == {
        "type": "urn:blizzard:problem:pinned-graph-missing",
        "title": "Pinned graph missing",
        "detail": "chunk's pinned graph is missing",
        "instance": path,
        "status": 500,
    }
    client.post("/_levers/legacy_read_refusals", json={"chunk_id": chunk, "remaining": 1})
    legacy = client.get(path, headers=headers) if body is None else client.post(path, headers=headers, json=body)
    assert legacy.status_code == 500
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": "chunk's pinned graph is missing"}
    modern = client.get(path, headers=headers) if body is None else client.post(path, headers=headers, json=body)
    assert modern.json() == response.json()
    assert client.get(f"/api/fleet/chunks/{chunk}", headers=headers).json() == before
    healed = client.get(path, headers=headers) if body is None else client.post(path, headers=headers, json=body)
    assert healed.status_code != 500


@pytest.mark.parametrize("status", ["done", "stopped", "delivering"])
def test_pause_refusal_modern_and_legacy(status: str) -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    spec = {"entry": "b", "nodes": {"b": {"executor": "hub"}}, "status": status}
    chunk = client.post("/_seed/chunk", json=spec).json()["chunk_id"]
    path = f"/api/fleet/chunks/{chunk}/pause"
    modern = client.post(path, headers=headers)
    assert modern.status_code == 409
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:chunk-not-pausable",
        "title": "Chunk not pausable",
        "detail": f"chunk {chunk} is {status}, not pausable",
        "instance": path,
        "status": 409,
    }
    client.post("/_levers/legacy_read_refusals", json={"chunk_id": chunk, "remaining": 1})
    legacy = client.post(path, headers=headers)
    assert legacy.status_code == 409
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": modern.json()["detail"]}
    assert client.post(path, headers=headers).json() == modern.json()
    assert client.get(f"/api/fleet/chunks/{chunk}", headers=headers).json()["status"] == status


def test_pause_resume_keep_route_and_missing_chunk_refusals() -> None:
    client = TestClient(create_app())
    runner = client.post("/api/runners", json={"name": "runner"}).json()
    headers = {"Authorization": f"Bearer {runner['token']}"}
    client.post(
        "/api/fleet/runners",
        headers=headers,
        json={"workspace_id": "w", "capabilities": [{"harness_id": "claude_code", "default": True}]},
    )
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    assert (
        client.post("/api/fleet/routes", headers=headers, json={"chunk_id": chunk, "workspace_id": "w"}).status_code
        == 201
    )
    path = f"/api/fleet/chunks/{chunk}"
    paused = client.post(f"{path}/pause", headers=headers)
    assert paused.status_code == 202
    assert paused.json()["status"] == "paused"
    assert paused.json()["runner_id"] == runner["runner_id"]
    assert client.post(f"{path}/pause", headers=headers).json()["status"] == "paused"
    resumed = client.post(f"{path}/resume", headers=headers)
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "running"
    assert resumed.json()["runner_id"] == runner["runner_id"]
    assert client.get(path, headers=headers).json()["route"]["runner_id"] == runner["runner_id"]
    for verb in ("pause", "resume"):
        missing = client.post(f"/api/fleet/chunks/missing/{verb}", headers=headers)
        assert missing.status_code == 404
        assert missing.json()["type"] == "urn:blizzard:problem:chunk-not-found"
        client.post("/_levers/legacy_read_refusals", json={"chunk_id": "missing", "remaining": 1})
        legacy = client.post(f"/api/fleet/chunks/missing/{verb}", headers=headers)
        assert legacy.status_code == 404
        assert legacy.headers["content-type"] == "application/json"
        assert legacy.json() == {"detail": missing.json()["detail"]}


def test_pause_needs_human_and_resume_seeded_paused() -> None:
    client = TestClient(create_app())
    token = client.post("/api/runners", json={"name": "runner"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    spec = {"entry": "b", "nodes": {"b": {"executor": "hub"}}}
    human = client.post("/_seed/chunk", json={**spec, "status": "needs_human"}).json()["chunk_id"]
    assert client.post(f"/api/fleet/chunks/{human}/pause", headers=headers).json()["status"] == "needs_human"
    assert client.post(f"/api/fleet/chunks/{human}/resume", headers=headers).json()["status"] == "needs_human"
    paused = client.post("/_seed/chunk", json={**spec, "status": "paused"}).json()["chunk_id"]
    assert client.post(f"/api/fleet/chunks/{paused}/resume", headers=headers).json()["status"] == "ready"


def test_host_retirement_refusal_and_retry_of_interrupted_retirement() -> None:
    client = TestClient(create_app())
    host = client.post("/api/hosts", json={"name": "host"}).json()
    host_headers = {"Authorization": f"Bearer {host['token']}"}
    runner = client.post("/api/fleet/host/runners", headers=host_headers, json={"name": "runner"}).json()
    headers = {"Authorization": f"Bearer {runner['token']}"}
    client.post(
        "/api/fleet/runners",
        headers=headers,
        json={"workspace_id": "w", "capabilities": [{"harness_id": "claude_code", "default": True}]},
    )
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    assert (
        client.post("/api/fleet/routes", headers=headers, json={"chunk_id": chunk, "workspace_id": "w"}).status_code
        == 201
    )
    path = f"/api/fleet/host/runners/{runner['runner_id']}/retire"
    detail = (
        f"runner {runner['runner_id']} holds 1 chunk(s): {chunk} (environments: none) — retire with --force to release"
    )
    modern = client.post(path, headers=host_headers, json={"by": "first"})
    assert modern.status_code == 409
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:runner-holds-routes",
        "title": "Runner holds routes",
        "detail": detail,
        "instance": path,
        "status": 409,
    }
    client.post("/_levers/legacy_host_runner_refusals", json={"remaining": 1})
    legacy = client.post(path, headers=host_headers, json={"by": "first"})
    assert legacy.status_code == 409
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": detail}
    assert client.post(path, headers=host_headers, json={"by": "first"}).json() == modern.json()
    assert client.get(f"/api/fleet/chunks/{chunk}", headers=headers).json()["route"]["runner_id"] == runner["runner_id"]

    # A retirement committed before its route-release pass is retried without force.
    client.post(f"/_seed/runners/{runner['runner_id']}/retire", json={"by": "first"})
    retried = client.post(path, headers=host_headers, json={"by": "second"})
    assert retried.status_code == 200
    assert retried.json()["released_chunk_ids"] == [chunk]
    assert retried.json()["runner"]["retired_by"] == "first"
    assert client.post(path, headers=host_headers, json={"by": "third"}).json()["released_chunk_ids"] == []


@pytest.mark.parametrize("interrupted", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
def test_host_retirement_lifecycle_refusals(interrupted: bool, legacy: bool) -> None:
    client = TestClient(create_app())
    host = client.post("/api/hosts", json={"name": "host"}).json()
    headers = {"Authorization": f"Bearer {host['token']}"}
    runner = client.post("/api/fleet/host/runners", headers=headers, json={"name": "runner"}).json()
    runner_headers = {"Authorization": f"Bearer {runner['token']}"}
    client.post(
        "/api/fleet/runners",
        headers=runner_headers,
        json={"workspace_id": "w", "capabilities": [{"harness_id": "claude_code", "default": True}]},
    )
    chunk = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "runner"}}}).json()["chunk_id"]
    assert (
        client.post(
            "/api/fleet/routes", headers=runner_headers, json={"chunk_id": chunk, "workspace_id": "w"}
        ).status_code
        == 201
    )
    path = f"/api/fleet/host/runners/{runner['runner_id']}/retire"
    client.post(
        "/_levers/host_retire_lifecycle_conflict", json={"remaining": 1, "payload": {"interrupted": interrupted}}
    )
    if legacy:
        client.post("/_levers/legacy_host_runner_refusals", json={"remaining": 1})
    response = client.post(path, headers=headers, json={"by": "first", "force": True})
    detail = f"runner {runner['runner_id']} changed after it was read — run it again"
    assert response.status_code == 409
    if legacy:
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"detail": detail}
    else:
        kind = "runner-retire-interrupted" if interrupted else "runner-lifecycle-conflict"
        title = "Runner retirement interrupted" if interrupted else "Runner lifecycle conflict"
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json() == {
            "type": f"urn:blizzard:problem:{kind}",
            "title": title,
            "detail": detail,
            "instance": path,
            "status": 409,
        }
    assert client.get("/api/fleet/identity", headers=runner_headers).status_code == (401 if interrupted else 200)
    retry = client.post(path, headers=headers, json={"by": "second", "force": True})
    assert retry.status_code == 200
    assert retry.json()["released_chunk_ids"] == [chunk]
    assert retry.json()["runner"]["retired_by"] == ("first" if interrupted else "second")
