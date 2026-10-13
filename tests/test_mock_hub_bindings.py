"""The mock hub's bindings: a registration serving the bound projects in binding order, flagging a binding the host
has not reported and a subscription outside its roster, and the matched peek withholding through a flag and narrowing
to one project — as the real hub does."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.domain.models import DEFAULT_PROJECT_SLUG
from tests.fleet_client import FleetClient
from tests.test_mock_hub_projects import _seed_chunk, _seed_project

_CAPABILITIES = [{"harness_id": "claude_code", "default": True}]


@pytest.fixture
def client() -> FleetClient:
    return FleetClient(create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC))))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bindings(*pairs: tuple[str, str]) -> list[dict[str, Any]]:
    return [{"project": project, "workspace_id": workspace, "agents": 1} for project, workspace in pairs]


class _World:
    """Two projects with a chunk each, and a runner under a host."""

    def __init__(self, client: FleetClient, *, hosted: bool = True) -> None:
        self.client = client
        self.web_id = _seed_project(client, "web")
        self.web = _seed_chunk(client, self.web_id)
        self.default = _seed_chunk(client)
        self.host = client.post("/api/hosts", json={"name": "box"}).json()
        added = client.post(
            "/api/runners", json={"name": "r", **({"host_id": self.host["host_id"]} if hosted else {})}
        ).json()
        self.token = added["token"]
        self.runner_id = added["runner_id"]

    def report(self, workspaces: list[str], roster: list[str] | None = None) -> None:
        resp = self.client.post(
            "/api/fleet/host",
            json={
                "name": "box",
                "workspaces": [{"workspace_id": w} for w in workspaces],
                "subscriptions": [
                    {"slug": s, "name": s, "provider": "anthropic"} for s in (["max"] if roster is None else roster)
                ],
            },
            headers=_bearer(self.host["token"]),
        )
        assert resp.status_code == 200, resp.text

    def register(self, **body: Any) -> dict[str, Any]:
        resp = self.client.post(
            "/api/fleet/runners",
            json={"workspace_id": "ws-a", "capabilities": _CAPABILITIES, **body},
            headers=_bearer(self.token),
        )
        assert resp.status_code == 201, resp.text
        return resp.json()

    def peek(self, **body: Any) -> list[str]:
        resp = self.client.post(
            "/api/fleet/queue/peek", json={"capabilities": _CAPABILITIES, **body}, headers=_bearer(self.token)
        )
        assert resp.status_code == 200, resp.text
        return [e["chunk_id"] for e in resp.json()["entries"]]


def test_a_registration_serves_the_bound_projects_in_binding_order_and_flags_what_the_host_lacks(
    client: FleetClient,
) -> None:
    world = _World(client)
    world.report(["ws-a"])

    answer = world.register(
        projects=["ignored"], bindings=_bindings(("web", "ws-a"), (DEFAULT_PROJECT_SLUG, "ws-x")), subscription="max"
    )

    assert [p["entry"] for p in answer["projects"]] == ["web", DEFAULT_PROJECT_SLUG]
    assert [(b["project"], b["workspace_unreported"]) for b in answer["bindings"]] == [
        ("web", False),
        (DEFAULT_PROJECT_SLUG, True),
    ]
    assert (answer["subscription"], answer["subscription_unrostered"]) == ("max", False)
    own = client.get(f"/api/fleet/runners/{world.runner_id}", headers=_bearer(world.token)).json()
    assert own["bindings"] == answer["bindings"]


def test_a_legacy_body_is_served_from_projects(client: FleetClient) -> None:
    world = _World(client)

    answer = world.register(projects=[DEFAULT_PROJECT_SLUG])

    assert (answer["bindings"], answer["subscription"], answer["subscription_unrostered"]) == (None, None, False)
    assert world.peek() == [world.default]


def test_a_runner_without_a_host_and_a_host_that_never_reported_are_never_flagged(client: FleetClient) -> None:
    world = _World(client)

    answer = world.register(bindings=_bindings(("web", "ws-x")), subscription="nope")

    assert answer["bindings"][0]["workspace_unreported"] is False
    assert answer["subscription_unrostered"] is False
    assert world.peek() == [world.web]


def test_the_peek_withholds_a_flagged_binding_until_the_host_reports_it_and_narrows_to_one_project(
    client: FleetClient,
) -> None:
    world = _World(client)
    world.report(["ws-a"])
    bindings = _bindings((DEFAULT_PROJECT_SLUG, "ws-a"), ("web", "ws-x"))
    world.register(bindings=bindings, subscription="max")

    assert world.peek() == [world.default]
    assert world.peek(project="web") == []
    assert world.peek(project="nope") == []

    world.report(["ws-a", "ws-x"])

    assert world.peek(project="web") == [world.web]
    assert world.peek(project=world.web_id) == [world.web]


def test_an_unrostered_subscription_withholds_every_binding(client: FleetClient) -> None:
    world = _World(client)
    world.report(["ws-a"])

    answer = world.register(bindings=_bindings((DEFAULT_PROJECT_SLUG, "ws-a"), ("web", "ws-a")), subscription="other")

    assert answer["subscription_unrostered"] is True
    assert world.peek() == []


def test_a_registration_without_bindings_clears_the_ones_held(client: FleetClient) -> None:
    world = _World(client)
    world.report(["ws-a"])
    world.register(bindings=_bindings(("web", "ws-x")))
    assert world.peek() == []

    answer = world.register(projects=["web"])

    assert answer["bindings"] is None
    assert world.peek() == [world.web]
