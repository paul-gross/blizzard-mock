"""The host-plane-absent switch: every ``/api/fleet/host`` route answers a route-missing 404, as a hub from before
hosts would, while every other route — ``POST /api/hosts`` included — answers as before."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.config import ENV_NO_HOST_PLANE, MockHubConfig
from tests.fleet_client import FleetClient

_REPORT: dict[str, Any] = {
    "name": "box",
    "url": "http://box.local:9000",
    "redirect_uris": [],
    "workspaces": [{"workspace_id": "w1", "env_capacity": 2}],
    "subscriptions": [],
}
_NOT_FOUND = {"detail": "Not Found"}


def _client(config: MockHubConfig | None = None) -> FleetClient:
    return FleetClient(create_app(config, clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC))))


@pytest.fixture
def client() -> FleetClient:
    return _client()


def _host_headers(client: FleetClient) -> dict[str, str]:
    resp = client.post("/api/hosts", json={"name": "box"})
    assert resp.status_code == 201, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def _host_plane(client: FleetClient, headers: dict[str, str], runner_id: str) -> list[Any]:
    """One answer from each of the five host-plane routes."""
    return [
        client.post("/api/fleet/host", json=_REPORT, headers=headers),
        client.get("/api/fleet/host", headers=headers),
        client.post("/api/fleet/host/events", json={"facts": []}, headers=headers),
        client.post("/api/fleet/host/runners", json={"name": "r-new"}, headers=headers),
        client.post(f"/api/fleet/host/runners/{runner_id}/retire", json={}, headers=headers),
    ]


def _a_hosted_runner(client: FleetClient, headers: dict[str, str]) -> str:
    resp = client.post("/api/fleet/host/runners", json={"name": "r-held"}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["runner_id"]


def test_switch_off_the_host_plane_answers(client: FleetClient) -> None:
    headers = _host_headers(client)
    runner_id = _a_hosted_runner(client, headers)

    assert [r.status_code for r in _host_plane(client, headers, runner_id)] == [200, 200, 200, 201, 200]


def test_switch_on_every_host_route_is_a_missing_route_and_the_rest_answer(client: FleetClient) -> None:
    headers = _host_headers(client)
    runner_id = _a_hosted_runner(client, headers)

    armed = client.post("/_levers/host_plane_absent", json={})
    assert armed.status_code == 200 and "armed" in armed.json()

    for resp in _host_plane(client, headers, runner_id):
        assert resp.status_code == 404
        assert resp.json() == _NOT_FOUND
    assert client.get("/api/fleet/queue/peek").status_code == 200
    assert client.post("/api/hosts", json={"name": "other"}).status_code == 201


def test_clearing_the_lever_restores_the_host_plane(client: FleetClient) -> None:
    headers = _host_headers(client)
    runner_id = _a_hosted_runner(client, headers)

    client.post("/_levers/host_plane_absent", json={})
    assert client.get("/api/fleet/host", headers=headers).status_code == 404
    client.delete("/_levers/host_plane_absent")

    assert [r.status_code for r in _host_plane(client, headers, runner_id)] == [200, 200, 200, 201, 200]


def test_the_startup_switch_holds_the_host_plane_absent_past_a_lever_reset() -> None:
    client = _client(MockHubConfig(host_plane_absent=True))
    headers = _host_headers(client)
    client.post("/_levers/reset")

    resp = client.get("/api/fleet/host", headers=headers)
    assert (resp.status_code, resp.json()) == (404, _NOT_FOUND)
    assert client.get("/api/fleet/queue/peek").status_code == 200


def test_the_startup_switch_reads_its_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_NO_HOST_PLANE, "1")
    assert MockHubConfig.from_env().host_plane_absent is True
    monkeypatch.delenv(ENV_NO_HOST_PLANE)
    assert MockHubConfig.from_env().host_plane_absent is False
