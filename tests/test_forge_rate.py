"""Conditional reads and the rate headers (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.forge.domain.clock import FixedClock
from blizzard_mock.forge.domain.rate import DEFAULT_RATE_LIMIT
from tests.forge_world import REPO, START, forge, open_pull

URL = f"/repos/{REPO}"


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[TestClient, FixedClock]]:
    clock = FixedClock(START)
    with forge(tmp_path, clock) as client:
        yield client, clock


def _remaining(resp: object, header: str = "X-RateLimit-Remaining") -> int:
    return int(resp.headers[header])  # type: ignore[attr-defined]


def _as(token: str) -> dict[str, str]:
    return {"Authorization": f"token {token}"}


def test_a_counted_answer_carries_the_four_headers_and_counts_down(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    first = client.get(URL, headers=_as("t1"))
    second = client.get(URL, headers=_as("t1"))
    assert first.headers["X-RateLimit-Limit"] == str(DEFAULT_RATE_LIMIT)
    assert first.headers["X-RateLimit-Resource"] == "core"
    assert int(first.headers["X-RateLimit-Reset"]) == int(START.timestamp()) + 3600
    assert _remaining(first) == DEFAULT_RATE_LIMIT - 1
    assert _remaining(second) == DEFAULT_RATE_LIMIT - 2
    assert second.headers["X-RateLimit-Reset"] == first.headers["X-RateLimit-Reset"]
    # Writes and errors count too.
    assert _remaining(client.get(f"{URL}/pulls/99", headers=_as("t1"))) == DEFAULT_RATE_LIMIT - 3
    assert _remaining(client.post(f"{URL}/pulls", json={"title": "x", "head": "ghost", "base": "main"})) < (
        DEFAULT_RATE_LIMIT
    )


def test_buckets_count_independently_per_credential(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    client.get(URL, headers=_as("t1"))
    client.get(URL, headers=_as("t1"))
    assert _remaining(client.get(URL, headers=_as("t2"))) == DEFAULT_RATE_LIMIT - 1
    assert _remaining(client.get(URL, headers={"Authorization": "Bearer t2"})) == DEFAULT_RATE_LIMIT - 2
    anon = client.get(URL)
    assert _remaining(anon) == DEFAULT_RATE_LIMIT - 1
    assert _remaining(client.get(URL)) == DEFAULT_RATE_LIMIT - 2


def test_a_matching_etag_answers_304_without_spending(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    first = client.get(URL, headers=_as("t1"))
    etag = first.headers["ETag"]
    cached = client.get(URL, headers={**_as("t1"), "If-None-Match": etag})
    assert cached.status_code == 304
    assert cached.headers["ETag"] == etag
    assert cached.content == b""
    assert _remaining(cached) == _remaining(first)
    assert _remaining(client.get(URL, headers=_as("t1"))) == _remaining(first) - 1
    stale = client.get(URL, headers={**_as("t1"), "If-None-Match": '"nope"'})
    assert stale.status_code == 200


def test_a_changed_body_changes_the_etag(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    pull = open_pull(client, "ahead")["number"]
    before = client.get(f"{URL}/pulls/{pull}")
    again = client.get(f"{URL}/pulls/{pull}")
    assert before.headers["ETag"] == again.headers["ETag"]
    client.post(f"{URL}/issues/{pull}/comments", json={"body": "hi"})
    client.patch(f"{URL}/pulls/{pull}", json={"state": "closed"})
    assert client.get(f"{URL}/pulls/{pull}").headers["ETag"] != before.headers["ETag"]


def test_only_the_repos_surface_is_conditional_and_counted(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    for path in ("/_levers", "/healthz"):
        resp = client.get(path)
        assert "ETag" not in resp.headers
        assert "X-RateLimit-Remaining" not in resp.headers
    page = client.get(f"/{REPO}/pull/1")
    assert "X-RateLimit-Remaining" not in page.headers


def test_the_window_restores_at_its_reset(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    client.get(URL, headers=_as("t1"))
    client.get(URL, headers=_as("t1"))
    clock.tick(3599)
    assert _remaining(client.get(URL, headers=_as("t1"))) == DEFAULT_RATE_LIMIT - 3
    clock.tick(1)
    renewed = client.get(URL, headers=_as("t1"))
    assert _remaining(renewed) == DEFAULT_RATE_LIMIT - 1
    assert int(renewed.headers["X-RateLimit-Reset"]) == int(START.timestamp()) + 3600 + 3600


def test_counting_to_zero_refuses_nothing(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    client.post("/_levers/rate_budget", json={"token": "t1", "remaining": 1})
    assert _remaining(client.get(URL, headers=_as("t1"))) == 0
    again = client.get(URL, headers=_as("t1"))
    assert again.status_code == 200
    assert _remaining(again) == 0


def test_rate_budget_sets_the_remaining_and_the_reset(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    reset = int(START.timestamp()) + 120
    client.post("/_levers/rate_budget", json={"token": "t1", "remaining": 7, "reset": reset})
    resp = client.get(URL, headers=_as("t1"))
    assert _remaining(resp) == 6
    assert int(resp.headers["X-RateLimit-Reset"]) == reset
    assert _remaining(client.get(URL, headers=_as("t2"))) == DEFAULT_RATE_LIMIT - 1


def test_rate_budget_with_no_token_addresses_every_bucket(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    client.get(URL, headers=_as("t1"))
    client.post("/_levers/rate_budget", json={"remaining": 10})
    assert _remaining(client.get(URL, headers=_as("t1"))) == 9
    assert _remaining(client.get(URL, headers=_as("t2"))) == 9
    assert _remaining(client.get(URL)) == 9


def test_reset_clears_the_ledger(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    client.get(URL, headers=_as("t1"))
    client.post("/_levers/rate_budget", json={"remaining": 3})
    client.post("/_levers/reset")
    assert _remaining(client.get(URL, headers=_as("t1"))) == DEFAULT_RATE_LIMIT - 1


def test_secondary_rate_limit_is_a_403_with_remaining_left(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    spent = client.get(URL, headers=_as("t1"))
    client.post("/_levers/secondary_rate_limited", json={})
    limited = client.get(URL, headers=_as("t1"))
    assert limited.status_code == 403
    assert "secondary rate limit" in limited.json()["message"]
    assert _remaining(limited) == _remaining(spent) > 0
    assert "Retry-After" in limited.headers
    # Refusals are never counted.
    client.delete("/_levers/secondary_rate_limited")
    assert _remaining(client.get(URL, headers=_as("t1"))) == _remaining(spent) - 1


def test_secondary_rate_limit_self_expires(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    client.post("/_levers/secondary_rate_limited", json={"remaining": 1})
    assert client.get(URL).status_code == 403
    assert client.get(URL).status_code == 200


def test_rate_limited_answers_a_real_reset_from_the_ledger(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    client.get(URL, headers=_as("t1"))
    client.post("/_levers/rate_limited", json={})
    limited = client.get(URL, headers=_as("t1"))
    assert limited.status_code == 403
    assert _remaining(limited) == 0
    assert int(limited.headers["X-RateLimit-Reset"]) == int(START.timestamp()) + 3600
