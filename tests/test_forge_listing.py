"""Pull listings that honour their parameters (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.forge.domain.clock import FixedClock
from tests.forge_world import REPO, START, forge, open_pull


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[TestClient, FixedClock]]:
    clock = FixedClock(START)
    with forge(tmp_path, clock) as client:
        yield client, clock


def _three_pulls(client: TestClient, clock: FixedClock) -> list[int]:
    numbers = []
    for head in ("ahead", "ahead2", "feature"):
        numbers.append(open_pull(client, head)["number"])
        clock.tick(10)
    return numbers


def _numbers(resp: object) -> list[int]:
    return [p["number"] for p in resp.json()]  # type: ignore[attr-defined]


def test_no_sort_answers_newest_first(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    a, b, c = _three_pulls(client, clock)
    assert _numbers(client.get(f"/repos/{REPO}/pulls")) == [c, b, a]
    assert _numbers(client.get(f"/repos/{REPO}/pulls", params={"direction": "asc"})) == [a, b, c]


def test_filter_by_base_and_by_owner_qualified_head(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    a = open_pull(client, "ahead")["number"]
    b = open_pull(client, "feature", base="ahead2")["number"]
    assert _numbers(client.get(f"/repos/{REPO}/pulls", params={"base": "ahead2"})) == [b]
    assert _numbers(client.get(f"/repos/{REPO}/pulls", params={"base": "main"})) == [a]
    assert _numbers(client.get(f"/repos/{REPO}/pulls", params={"head": "octocat:ahead"})) == [a]
    assert client.get(f"/repos/{REPO}/pulls", params={"head": "stranger:ahead"}).json() == []


def test_sort_by_updated_in_both_directions(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    a, b, c = _three_pulls(client, clock)
    client.patch(f"/repos/{REPO}/pulls/{a}", json={"state": "closed"})
    client.patch(f"/repos/{REPO}/pulls/{a}", json={"state": "open"})
    params = {"sort": "updated"}
    # GitHub's pulls API defaults `direction` to asc for any sort but created.
    assert _numbers(client.get(f"/repos/{REPO}/pulls", params=params)) == [b, c, a]
    assert _numbers(client.get(f"/repos/{REPO}/pulls", params={**params, "direction": "desc"})) == [a, c, b]


def test_updated_at_moves_with_every_state_change(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world

    def updated(number: int) -> str:
        return client.get(f"/repos/{REPO}/pulls/{number}").json()["updated_at"]

    first = open_pull(client, "ahead")
    created = first["updated_at"]
    clock.tick(60)
    client.patch(f"/repos/{REPO}/pulls/{first['number']}", json={"state": "closed"})
    closed = updated(first["number"])
    clock.tick(60)
    client.patch(f"/repos/{REPO}/pulls/{first['number']}", json={"state": "open"})
    reopened = updated(first["number"])
    clock.tick(60)
    client.put(f"/repos/{REPO}/pulls/{first['number']}/merge", json={})
    merged = updated(first["number"])
    second = open_pull(client, "ahead2")
    clock.tick(60)
    client.post("/_levers/externally_merged", json={"repo": REPO, "number": second["number"]})
    external = updated(second["number"])
    stamps = [created, closed, reopened, merged]
    assert stamps == sorted(set(stamps))
    assert external > second["updated_at"]


def test_paging_carries_a_link_header_until_the_last_page(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    a, b, c = _three_pulls(client, clock)
    first = client.get(f"/repos/{REPO}/pulls", params={"per_page": 1})
    assert _numbers(first) == [c]
    links = {rel: url for url, rel in _links(first.headers["Link"])}
    assert set(links) == {"next", "last"}
    assert parse_qs(urlparse(links["next"]).query) == {"per_page": ["1"], "page": ["2"]}
    assert parse_qs(urlparse(links["last"]).query)["page"] == ["3"]

    middle = client.get(f"/repos/{REPO}/pulls", params={"per_page": 1, "page": 2})
    assert _numbers(middle) == [b]
    assert {rel for _, rel in _links(middle.headers["Link"])} == {"next", "last", "prev", "first"}

    last = client.get(f"/repos/{REPO}/pulls", params={"per_page": 1, "page": 3})
    assert _numbers(last) == [a]
    assert {rel for _, rel in _links(last.headers["Link"])} == {"prev", "first"}


def test_per_page_is_capped_and_a_single_page_has_no_link(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    _three_pulls(client, clock)
    everything = client.get(f"/repos/{REPO}/pulls", params={"per_page": 500})
    assert len(everything.json()) == 3
    assert "Link" not in everything.headers


def test_no_per_page_answers_every_match_on_one_page(world: tuple[TestClient, FixedClock]) -> None:
    client, clock = world
    numbers = _three_pulls(client, clock)
    resp = client.get(f"/repos/{REPO}/pulls")
    assert len(resp.json()) == len(numbers)
    assert "Link" not in resp.headers
    assert client.get(f"/repos/{REPO}/pulls", params={"page": 2}).json() == []


def test_bad_sort_is_422(world: tuple[TestClient, FixedClock]) -> None:
    client, _ = world
    assert client.get(f"/repos/{REPO}/pulls", params={"sort": "popularity"}).status_code == 422


def _links(header: str) -> list[tuple[str, str]]:
    out = []
    for part in header.split(", "):
        url, _, rel = part.partition("; rel=")
        out.append((url.strip("<>"), rel.strip('"')))
    return out
