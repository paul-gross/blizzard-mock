from fastapi.testclient import TestClient

from blizzard_mock.mock_hub.app import create_app


def test_summary_counts_all_projects_in_callers_tenant() -> None:
    client = TestClient(create_app())
    runner = client.post("/_seed/runners", json={"name": "one", "token": "one"}).json()
    other_tenant = client.post("/_seed/tenants", json={"name": "other"}).json()["tenant_id"]
    other_runner = client.post("/_seed/runners", json={"name": "two", "token": "two", "tenant_id": other_tenant}).json()
    project = client.post("/_seed/projects", json={"slug": "extra", "name": "Extra"}).json()["project_id"]
    foreign_project = client.post(
        "/_seed/projects", json={"slug": "foreign", "name": "Foreign", "tenant_id": other_tenant}
    ).json()["project_id"]
    headers = {"Authorization": "Bearer one"}
    assert client.get("/api/fleet/summary", headers=headers).json() == {
        "ready": 0,
        "running": 0,
        "waiting": 0,
        "needs": 0,
    }
    spec = {"entry": "build", "nodes": {"build": {"executor": "runner"}}}
    for status in ("ready", "delivering", "paused", "needs_human", "not_ready", "done", "stopped"):
        response = client.post("/_seed/chunk", json={**spec, "status": status, "project_id": project})
        assert response.status_code == 201, response.text
    assert client.post("/_seed/chunk", json={**spec, "project_id": foreign_project}).status_code == 201
    claimable = client.post("/_seed/chunk", json=spec).json()["chunk_id"]
    assert (
        client.post(
            "/api/fleet/runners",
            headers=headers,
            json={"capabilities": [{"harness_id": "claude_code", "default": True}]},
        ).status_code
        == 201
    )
    claim = client.post("/api/fleet/routes", headers=headers, json={"chunk_id": claimable})
    assert claim.status_code == 201, claim.text
    assert client.get("/api/fleet/summary", headers=headers).json() == {
        "ready": 1,
        "running": 2,
        "waiting": 1,
        "needs": 1,
    }
    assert client.get("/api/fleet/summary", headers={"Authorization": "Bearer two"}).json() == {
        "ready": 1,
        "running": 0,
        "waiting": 0,
        "needs": 0,
    }
    assert runner["runner_id"] != other_runner["runner_id"]


def test_summary_auth_refusals_modern_and_legacy() -> None:
    client = TestClient(create_app())
    path = "/api/fleet/summary"
    modern = client.get(path)
    assert modern.status_code == 401
    assert modern.headers["content-type"] == "application/problem+json"
    assert modern.json() == {
        "type": "urn:blizzard:problem:authentication-required",
        "title": "Authentication required",
        "detail": "missing or malformed Authorization header",
        "instance": path,
        "status": 401,
    }
    assert client.post("/_levers/legacy_auth_refusals", json={"remaining": 1}).status_code == 200
    legacy = client.get(path)
    assert legacy.status_code == 401
    assert legacy.headers["content-type"] == "application/json"
    assert legacy.json() == {"detail": modern.json()["detail"]}
    assert client.get(path).headers["content-type"] == "application/problem+json"

    unknown = client.get(path, headers={"Authorization": "Bearer unknown"})
    assert unknown.status_code == 401
    assert unknown.json()["type"] == "urn:blizzard:problem:authentication-unresolved"
    runner = client.post("/_seed/runners", json={"name": "one", "token": "one"}).json()
    headers = {"Authorization": "Bearer one", "X-Blizzard-Tenant": "another"}
    mismatch = client.get(path, headers=headers)
    assert mismatch.status_code == 400
    assert mismatch.json()["type"] == "urn:blizzard:problem:tenant-credential-mismatch"
    client.post("/_levers/legacy_auth_refusals", json={"remaining": 1})
    assert client.get(path, headers=headers).json() == {"detail": mismatch.json()["detail"]}
    client.post(f"/_seed/runners/{runner['runner_id']}/token-revocations")
    revoked = client.get(path, headers={"Authorization": "Bearer one"})
    assert revoked.status_code == 401
    assert revoked.json()["type"] == "urn:blizzard:problem:authentication-revoked"
