"""The mock hub's projects: the registry every tenant starts with, the projects a runner serves, the
matched peek narrowed to them, and the claim refused for a project the runner does not serve."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.domain.models import DEFAULT_PROJECT_NAME, DEFAULT_PROJECT_SLUG, FIRST_PROJECT_ID
from blizzard_mock.mock_hub.domain.service import TENANT_HEADER
from tests.fleet_client import FleetClient, register_runner

_CAPABILITIES = [{"harness_id": "claude_code", "default": True}]

_DEFAULT_SERVED = {
    "entry": FIRST_PROJECT_ID,
    "project_id": FIRST_PROJECT_ID,
    "slug": DEFAULT_PROJECT_SLUG,
    "name": DEFAULT_PROJECT_NAME,
}


@pytest.fixture
def client() -> FleetClient:
    return FleetClient(create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC))))


def _seed_project(client: FleetClient, slug: str, *, tenant_id: str | None = None) -> str:
    body = {"slug": slug, "name": slug.title()}
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    resp = client.post("/_seed/projects", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["project_id"]


def _seed_chunk(client: FleetClient, project_id: str | None = None) -> str:
    spec: dict = {
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "b",
                "judgement_prompt": "j",
                "choices": [{"name": "pass", "description": "p", "to": "done"}],
            }
        },
    }
    if project_id is not None:
        spec["project_id"] = project_id
    resp = client.post("/_seed/chunk", json=spec)
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _register(client: FleetClient, runner_id: str = "r1", **declared: object) -> httpx.Response:
    """Register ``runner_id`` — added on first use, as the test client does, when no test added it."""
    resp = client.post(
        "/api/fleet/runners",
        json={"runner_id": runner_id, "workspace_id": "ws", "capabilities": _CAPABILITIES, **declared},
    )
    assert resp.status_code == 201, resp.text
    return resp


def _register_added(client: FleetClient, added: httpx.Response) -> httpx.Response:
    """Register the runner ``POST /api/runners`` added, with the token it minted."""
    assert added.status_code == 201, added.text
    resp = client.post(
        "/api/fleet/runners",
        json={"workspace_id": "ws", "capabilities": _CAPABILITIES},
        headers={"Authorization": f"Bearer {added.json()['token']}"},
    )
    assert resp.status_code == 201, resp.text
    return resp


def _peek(client: FleetClient, runner_id: str = "r1", *, policy: str = "pass_over") -> list[dict]:
    resp = client.post(
        "/api/fleet/queue/peek", params={"runner_id": runner_id}, json={"capabilities": _CAPABILITIES, "policy": policy}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["entries"]


# --- the registry ------------------------------------------------------------


def test_the_first_tenant_starts_with_its_default_project_and_a_reset_restores_it(client: FleetClient) -> None:
    other = _seed_project(client, "other")
    assert client.post("/_seed/reset").status_code == 200

    assert client.get(f"/api/fleet/chunks/{_seed_chunk(client)}").json()["project_id"] == FIRST_PROJECT_ID
    resp = client.post("/_seed/chunk", json={"entry": "b", "nodes": {"b": {"executor": "hub"}}, "project_id": other})
    assert resp.status_code == 404, resp.text


def test_a_seeded_tenant_starts_with_a_default_project_of_its_own(client: FleetClient) -> None:
    tenant_id = client.post("/_seed/tenants", json={"name": "Second"}).json()["tenant_id"]
    added = client.post("/api/runners", json={"name": "r"}, headers={TENANT_HEADER: tenant_id})

    resp = _register_added(client, added)

    (served,) = resp.json()["projects"]
    assert served["slug"] == DEFAULT_PROJECT_SLUG
    assert served["project_id"] != FIRST_PROJECT_ID
    assert served["entry"] == served["project_id"]


def test_seeding_a_project_refuses_a_slug_the_tenant_holds_and_an_unknown_tenant(client: FleetClient) -> None:
    assert client.post("/_seed/projects", json={"slug": DEFAULT_PROJECT_SLUG, "name": "Again"}).status_code == 409
    resp = client.post("/_seed/projects", json={"slug": "x", "name": "X", "tenant_id": "ten_unknown"})
    assert resp.status_code == 404


def test_chunk_reads_carry_the_chunk_project(client: FleetClient) -> None:
    project_id = _seed_project(client, "web")
    chunk_id = _seed_chunk(client, project_id)

    detail = client.get(f"/api/fleet/chunks/{chunk_id}").json()
    (status,) = client.get("/api/fleet/chunk-statuses", params={"chunk_id": chunk_id}).json()

    for view in (detail, status):
        assert (view["project_id"], view["project_slug"], view["project_name"]) == (project_id, "web", "Web")


# --- the declaration a runner is added with ----------------------------------


def test_a_runner_added_naming_no_project_serves_the_tenant_only_project(client: FleetClient) -> None:
    resp = _register_added(client, client.post("/api/runners", json={"name": "solo"}))

    assert resp.json()["projects"] == [_DEFAULT_SERVED]


def test_a_runner_added_naming_none_in_a_tenant_of_several_projects_serves_nothing(client: FleetClient) -> None:
    _seed_project(client, "web")
    added = client.post("/api/runners", json={"name": "unassigned"})

    assert _register_added(client, added).json()["projects"] == []


def test_a_runner_added_naming_projects_serves_exactly_those(client: FleetClient) -> None:
    web = _seed_project(client, "web")
    added = client.post("/api/runners", json={"name": "named", "projects": ["web", FIRST_PROJECT_ID]})

    resp = _register_added(client, added)

    assert resp.json()["projects"] == [
        {"entry": "web", "project_id": web, "slug": "web", "name": "Web"},
        _DEFAULT_SERVED,
    ]


def test_adding_a_runner_naming_an_unknown_project_is_refused_422_and_adds_nothing(client: FleetClient) -> None:
    before = len(client.service._state.list_runners())

    resp = client.post("/api/runners", json={"name": "lost", "projects": ["default", "nowhere"]})

    assert resp.status_code == 422, resp.text
    assert "nowhere" in resp.json()["detail"]
    assert len(client.service._state.list_runners()) == before


# --- the declaration at registration -----------------------------------------


def test_registration_declaring_none_keeps_the_held_declaration(client: FleetClient) -> None:
    assert _register(client).json()["projects"] == [_DEFAULT_SERVED]
    assert _register(client, projects=None).json()["projects"] == [_DEFAULT_SERVED]


def test_registration_declaring_an_empty_list_serves_nothing(client: FleetClient) -> None:
    assert _register(client, projects=[]).json()["projects"] == []
    # a later registration declaring none keeps the empty declaration rather than restoring the default.
    assert _register(client).json()["projects"] == []


def test_registration_declaring_a_list_replaces_the_declaration(client: FleetClient) -> None:
    web = _seed_project(client, "web")

    resp = _register(client, projects=[web])

    assert resp.json()["projects"] == [{"entry": web, "project_id": web, "slug": "web", "name": "Web"}]
    assert client.get("/api/fleet/runners/r1").json()["projects"] == resp.json()["projects"]


def test_an_unresolved_entry_is_kept_serving_nothing_until_a_project_answers_to_it(client: FleetClient) -> None:
    unresolved = _register(client, projects=["later"])
    assert unresolved.json()["projects"] == [{"entry": "later", "project_id": None, "slug": None, "name": None}]

    later = _seed_project(client, "later")
    resolved = _register(client, projects=["later"])

    assert resolved.json()["projects"] == [{"entry": "later", "project_id": later, "slug": "later", "name": "Later"}]


def test_a_project_of_another_tenant_resolves_to_nothing(client: FleetClient) -> None:
    tenant_id = client.post("/_seed/tenants", json={"name": "Second"}).json()["tenant_id"]
    foreign = _seed_project(client, "foreign", tenant_id=tenant_id)

    (served,) = _register(client, projects=[foreign]).json()["projects"]

    assert served == {"entry": foreign, "project_id": None, "slug": None, "name": None}


# --- the matched peek ----------------------------------------------------------


def test_the_matched_peek_offers_only_chunks_of_served_projects(client: FleetClient) -> None:
    web = _seed_project(client, "web")
    _seed_chunk(client, web)
    mine = _seed_chunk(client)
    _register(client, projects=[DEFAULT_PROJECT_SLUG])

    (entry,) = _peek(client)

    assert entry["chunk_id"] == mine
    assert entry["position"] == 1  # its whole-order index, behind the other project's chunk
    assert (entry["project_id"], entry["project_slug"], entry["project_name"]) == (
        FIRST_PROJECT_ID,
        DEFAULT_PROJECT_SLUG,
        DEFAULT_PROJECT_NAME,
    )


def test_hold_is_never_stalled_by_another_project_head(client: FleetClient) -> None:
    web = _seed_project(client, "web")
    _seed_chunk(client, web)
    mine = _seed_chunk(client)
    _register(client, projects=[DEFAULT_PROJECT_SLUG])

    assert [e["chunk_id"] for e in _peek(client, policy="hold")] == [mine]


def test_a_runner_serving_nothing_is_offered_nothing(client: FleetClient) -> None:
    _seed_chunk(client)
    _register(client, projects=[])

    assert _peek(client) == []


def test_the_unmatched_peek_still_shows_every_project(client: FleetClient) -> None:
    web = _seed_project(client, "web")
    theirs = _seed_chunk(client, web)
    mine = _seed_chunk(client)

    assert [e["chunk_id"] for e in client.get("/api/fleet/queue/peek").json()["entries"]] == [theirs, mine]


# --- the claim recheck ---------------------------------------------------------


def test_a_claim_on_an_unserved_project_is_refused_with_the_project_reason(client: FleetClient) -> None:
    web = _seed_project(client, "web")
    chunk_id = _seed_chunk(client, web)
    register_runner(client.service, "r1")

    denied = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert denied.status_code == 409, denied.text
    body = denied.json()
    assert body["chunk_id"] == chunk_id
    assert body["incompatible_runner_id"] == "r1"
    assert body["reason"] == "project"
    assert "web" in body["detail"]
    assert client.get(f"/api/fleet/chunks/{chunk_id}").json()["route"] is None


def test_a_claim_refused_for_capabilities_names_the_capability_reason(client: FleetClient) -> None:
    resp = client.post(
        "/_seed/chunk",
        json={
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "prompt": "b",
                    "judgement_prompt": "j",
                    "session_harnesses": ["special_harness"],
                    "choices": [{"name": "pass", "description": "p", "to": "done"}],
                }
            },
        },
    )
    chunk_id = resp.json()["chunk_id"]
    register_runner(client.service, "r1")

    denied = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert denied.status_code == 409, denied.text
    assert denied.json()["reason"] == "capability"
    assert denied.json()["detail"] == "runner capabilities no longer satisfy the chunk's reachable lineage"


def test_a_claim_on_a_served_project_mints_the_route(client: FleetClient) -> None:
    web = _seed_project(client, "web")
    chunk_id = _seed_chunk(client, web)
    register_runner(client.service, "r1")
    assert client.put("/_seed/runners/r1/projects", json={"projects": ["web"]}).status_code == 200

    claimed = client.post("/api/fleet/routes", json={"chunk_id": chunk_id, "runner_id": "r1"})

    assert claimed.status_code == 201, claimed.text
