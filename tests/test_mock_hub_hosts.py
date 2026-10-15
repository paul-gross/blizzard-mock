"""The mock hub's hosts: ``POST /api/hosts``, a host's bearer token on the five ``/api/fleet/host*`` routes and on
nothing else, and a runner added under a host."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.clock import FixedClock
from blizzard_mock.mock_hub.app import create_app
from blizzard_mock.mock_hub.domain.service import TENANT_HEADER
from tests.fleet_client import FleetClient

_REPORT: dict[str, Any] = {
    "name": "box",
    "url": "http://box.local:9000",
    "redirect_uris": ["http://box.local:9000/callback"],
    "workspaces": [{"workspace_id": "w1", "env_capacity": 2}],
    "subscriptions": [{"slug": "claude-max", "name": "Claude Max", "provider": "anthropic"}],
}


@pytest.fixture
def client() -> FleetClient:
    return FleetClient(create_app(clock=FixedClock(datetime(2026, 7, 13, tzinfo=UTC))))


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add(client: TestClient, name: str = "box", **body: Any) -> dict[str, Any]:
    resp = client.post("/api/hosts", json={"name": name, **body})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_add_mints_a_prefixed_id_and_a_token_once(client: FleetClient) -> None:
    added = _add(client)

    assert added["host_id"].startswith("hs_")
    assert added["token"]
    assert added["host_name"] == "box"
    assert added["adopted"] == []


def test_a_blank_name_is_refused_and_a_live_name_is_taken(client: FleetClient) -> None:
    _add(client)

    assert client.post("/api/hosts", json={"name": " "}).status_code == 422
    assert client.post("/api/hosts", json={"name": "box"}).status_code == 409


def test_a_report_replaces_what_the_host_reported_whole_and_answers_its_record(client: FleetClient) -> None:
    added = _add(client)

    first = client.post("/api/fleet/host", json=_REPORT, headers=_bearer(added["token"]))
    record = client.get("/api/fleet/host", headers=_bearer(added["token"]))

    assert first.status_code == record.status_code == 200, first.text
    assert first.json()["host_id"] == added["host_id"]
    assert first.json()["tenant_id"].startswith("ten_")
    assert first.json()["registered_at"] is not None
    assert record.json() == first.json()


def test_a_name_another_live_host_holds_is_not_recorded(client: FleetClient) -> None:
    _add(client, "alpha")
    beta = _add(client, "beta")

    resp = client.post("/api/fleet/host", json={**_REPORT, "name": "alpha"}, headers=_bearer(beta["token"]))

    assert resp.json()["host_name"] == "beta"


def test_a_host_adds_a_runner_under_itself_that_registers_with_its_own_token(client: FleetClient) -> None:
    added = _add(client)
    headers = _bearer(added["token"])

    created = client.post("/api/fleet/host/runners", json={"name": "r-one"}, headers=headers)

    assert created.status_code == 201, created.text
    assert created.json()["runner_id"].startswith("rn_")
    record = client.get("/api/fleet/host", headers=headers).json()
    assert record["runners"] == [{"runner_id": created.json()["runner_id"], "runner_name": "r-one"}]
    registered = client.post(
        "/api/fleet/runners", json={"workspace_id": "w1"}, headers=_bearer(created.json()["token"])
    )
    assert registered.status_code == 201


def test_a_host_retires_a_runner_under_itself_and_no_other(client: FleetClient) -> None:
    added = _add(client)
    other = _add(client, "other")
    mine = client.post("/api/fleet/host/runners", json={"name": "m"}, headers=_bearer(added["token"])).json()
    theirs = client.post("/api/fleet/host/runners", json={"name": "t"}, headers=_bearer(other["token"])).json()

    refused = client.post(
        f"/api/fleet/host/runners/{theirs['runner_id']}/retire", json={}, headers=_bearer(added["token"])
    )
    retired = client.post(
        f"/api/fleet/host/runners/{mine['runner_id']}/retire", json={"by": "box"}, headers=_bearer(added["token"])
    )

    assert refused.status_code == 404
    assert retired.status_code == 200, retired.text
    assert retired.json()["runner"]["retired"] is True
    assert retired.json()["runner"]["connection"] == "never_connected"
    assert {"online", "added_at", "hub_paused", "retired_at"} <= retired.json()["runner"].keys()
    assert client.get("/api/fleet/host", headers=_bearer(added["token"])).json()["runners"] == []
    assert client.get("/api/fleet/identity", headers=_bearer(mine["token"])).status_code == 401


def test_a_host_token_reaches_no_runner_route_and_a_runner_token_no_host_route(client: FleetClient) -> None:
    added = _add(client)
    runner = client.post("/api/runners", json={"name": "r"}).json()

    assert (
        client.post("/api/fleet/runners", json={"workspace_id": "w"}, headers=_bearer(added["token"])).status_code
        == 401
    )
    assert client.get("/api/fleet/queue/peek", headers=_bearer(added["token"])).status_code == 401
    assert client.get("/api/fleet/host", headers=_bearer(runner["token"])).status_code == 401
    assert client.post("/api/fleet/host", json=_REPORT, headers=_bearer(runner["token"])).status_code == 401
    assert client.get("/api/fleet/host").status_code == 401


def test_a_foreign_tenant_header_and_an_unknown_token_are_refused(client: FleetClient) -> None:
    added = _add(client)
    other_tenant = client.post("/_seed/tenants", json={"name": "other"}).json()["tenant_id"]

    foreign = client.get("/api/fleet/host", headers={**_bearer(added["token"]), TENANT_HEADER: other_tenant})
    unknown = client.get("/api/fleet/host", headers=_bearer("nope"))

    assert foreign.status_code == 400
    assert unknown.status_code == 401


def test_a_runner_is_added_under_a_host_and_an_unknown_one_is_refused(client: FleetClient) -> None:
    added = _add(client)

    ok = client.post("/api/runners", json={"name": "r", "host_id": added["host_id"]})
    missing = client.post("/api/runners", json={"name": "r", "host_id": "hs_nope"})

    assert ok.status_code == 201, ok.text
    assert missing.status_code == 404
    record = client.get("/api/fleet/host", headers=_bearer(added["token"])).json()
    assert [r["runner_id"] for r in record["runners"]] == [ok.json()["runner_id"]]


def test_add_with_adopt_places_runners_and_a_refusal_adds_no_host(client: FleetClient) -> None:
    alone = client.post("/api/runners", json={"name": "a"}).json()["runner_id"]

    refused = client.post("/api/hosts", json={"name": "bad", "adopt": [alone, "rn_missing"]})
    added = _add(client, "good", adopt=[alone])
    again = client.post("/api/hosts", json={"name": "worse", "adopt": [alone]})

    assert refused.status_code == 404
    assert added["adopted"] == [alone]
    assert again.status_code == 409
    record = client.get("/api/fleet/host", headers=_bearer(added["token"])).json()
    assert [r["runner_id"] for r in record["runners"]] == [alone]


def test_a_reset_forgets_every_host_and_its_token(client: FleetClient) -> None:
    added = _add(client)

    assert client.post("/_seed/reset").status_code in {200, 204}

    assert client.get("/api/fleet/host", headers=_bearer(added["token"])).status_code == 401


def _sample(seq: int, slug: str = "anthropic") -> dict[str, Any]:
    return {
        "seq": seq,
        "kind": "external_subscription_usage.sampled",
        "payload": {
            "slug": slug,
            "sampled_at": "2026-07-13T00:00:00+00:00",
            "windows": [
                {
                    "window": "5h",
                    "utilization_pct": 12.0,
                    "resets_at": "2026-07-13T05:00:00+00:00",
                    "window_seconds": 18000,
                }
            ],
        },
    }


def test_a_host_pushes_usage_facts_against_a_mark_of_its_own(client: FleetClient) -> None:
    added = _add(client)
    headers = _bearer(added["token"])
    miss = {"seq": 2, "kind": "external_subscription_usage.missed", "payload": {"slug": "anthropic", "reason": "x"}}

    first = client.post("/api/fleet/host/events", json={"facts": [_sample(1), miss]}, headers=headers).json()
    replay = client.post("/api/fleet/host/events", json={"facts": [_sample(1), miss]}, headers=headers).json()

    assert first == {
        "host_id": added["host_id"],
        "high_water": 2,
        "applied": [1, 2],
        "already_applied": [],
        "rejected": [],
    }
    assert (replay["applied"], replay["already_applied"], replay["high_water"]) == ([], [1, 2], 2)


def test_a_host_lane_rejects_other_kinds_and_holds_the_mark_behind_them(client: FleetClient) -> None:
    added = _add(client)
    headers = _bearer(added["token"])
    other = {"seq": 2, "kind": "lease.minted", "payload": {}}
    no_slug = {"seq": 3, "kind": "external_subscription_usage.sampled", "payload": {}}

    ack = client.post("/api/fleet/host/events", json={"facts": [_sample(1), other, no_slug]}, headers=headers).json()

    assert (ack["applied"], ack["rejected"], ack["high_water"]) == ([1], [2, 3], 1)


def test_a_runner_token_and_a_retired_host_are_refused_the_host_lane(client: FleetClient) -> None:
    added = _add(client)
    runner = client.post("/api/runners", json={"name": "r"}).json()

    assert (
        client.post("/api/fleet/host/events", json={"facts": []}, headers=_bearer(runner["token"])).status_code == 401
    )
    assert client.post("/api/fleet/host/events", json={"facts": []}).status_code == 401
    assert client.post("/api/fleet/host/events", json={"facts": []}, headers=_bearer(added["token"])).status_code == 200


def test_a_hosted_runner_reads_its_hosts_sample_for_the_slug_it_registered(client: FleetClient) -> None:
    added = _add(client)
    host = _bearer(added["token"])
    assert client.post("/api/fleet/host", json=_REPORT, headers=host).status_code == 200
    spender = client.post("/api/fleet/host/runners", json={"name": "r1"}, headers=host).json()
    bare = client.post("/api/fleet/host/runners", json={"name": "r2"}, headers=host).json()
    for runner, subscription in ((spender, "claude-max"), (bare, None)):
        registered = client.post(
            "/api/fleet/runners",
            json={"workspace_id": "w1", "subscription": subscription},
            headers=_bearer(runner["token"]),
        )
        assert registered.status_code == 201, registered.text

    client.post("/api/fleet/host/events", json={"facts": [_sample(1, "claude-max")]}, headers=host)

    shown = client.get(f"/api/fleet/runners/{spender['runner_id']}", headers=_bearer(spender["token"])).json()
    none = client.get(f"/api/fleet/runners/{bare['runner_id']}", headers=_bearer(bare["token"])).json()
    assert [(s["slug"], s["windows"][0]["utilization_pct"]) for s in shown["subscriptions"]] == [("claude-max", 12.0)]
    assert none["subscriptions"] == []
