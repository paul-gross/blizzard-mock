"""The GraphQL batch read of issues and pulls (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.forge.domain.clock import FixedClock
from blizzard_mock.forge.domain.rate import DEFAULT_RATE_LIMIT
from tests.forge_world import REPO, START, forge, open_pull

OWNER, NAME = REPO.split("/")


@pytest.fixture
def world(tmp_path: Path) -> Iterator[TestClient]:
    with forge(tmp_path, FixedClock(START)) as client:
        yield client


def _query(numbers: list[int], *, comments: bool) -> str:
    selection = "title body" + (" comments(first: 100) { nodes { body } }" if comments else "")
    fields = " ".join(
        f"i{n}: issueOrPullRequest(number: {n}) {{ ... on Issue {{ {selection} }} ... on PullRequest {{ {selection} }} }}"
        for n in numbers
    )
    return f"query($owner: String!, $name: String!) {{ repository(owner: $owner, name: $name) {{ {fields} }} }}"


def _post(client: TestClient, numbers: list[int], *, comments: bool = False, token: str = "t1") -> Any:
    return client.post(
        "/graphql",
        json={"query": _query(numbers, comments=comments), "variables": {"owner": OWNER, "name": NAME}},
        headers={"Authorization": f"token {token}"},
    )


def _issue(client: TestClient, title: str, body: str = "") -> int:
    return int(client.post(f"/repos/{REPO}/issues", json={"title": title, "body": body}).json()["number"])


def test_a_batch_answers_present_missing_and_pull_numbers(world: TestClient) -> None:
    first = _issue(world, "first", "first body")
    pull = open_pull(world, "ahead")["number"]
    resp = _post(world, [first, pull, 999])
    assert resp.status_code == 200
    repo = resp.json()["data"]["repository"]
    assert repo[f"i{first}"] == {"title": "first", "body": "first body"}
    assert repo[f"i{pull}"]["title"]
    assert repo["i999"] is None
    errors = resp.json()["errors"]
    assert [(e["type"], e["path"]) for e in errors] == [("NOT_FOUND", ["repository", "i999"])]


def test_comments_come_only_when_selected(world: TestClient) -> None:
    number = _issue(world, "talked", "b")
    world.post(f"/repos/{REPO}/issues/{number}/comments", json={"body": "one"})
    world.post(f"/repos/{REPO}/issues/{number}/comments", json={"body": "two"})
    without = _post(world, [number]).json()["data"]["repository"][f"i{number}"]
    with_comments = _post(world, [number], comments=True).json()["data"]["repository"][f"i{number}"]
    assert "comments" not in without
    assert with_comments["comments"] == {"nodes": [{"body": "one"}, {"body": "two"}]}


def test_an_unsupported_query_answers_an_error_entry(world: TestClient) -> None:
    resp = world.post("/graphql", json={"query": "{ viewer { login } }", "variables": {}})
    assert resp.status_code == 200
    assert resp.json()["errors"][0]["type"] == "BAD_REQUEST"


def test_a_graphql_answer_spends_the_graphql_window_not_core(world: TestClient) -> None:
    number = _issue(world, "x")
    first = _post(world, [number])
    second = _post(world, [number])
    assert first.headers["X-RateLimit-Resource"] == "graphql"
    assert int(first.headers["X-RateLimit-Remaining"]) == DEFAULT_RATE_LIMIT - 1
    assert int(second.headers["X-RateLimit-Remaining"]) == DEFAULT_RATE_LIMIT - 2
    core = world.get(f"/repos/{REPO}", headers={"Authorization": "token t1"})
    assert core.headers["X-RateLimit-Resource"] == "core"
    assert int(core.headers["X-RateLimit-Remaining"]) == DEFAULT_RATE_LIMIT - 1


def test_an_exhausted_window_answers_rate_limited_with_the_reset(world: TestClient) -> None:
    number = _issue(world, "x")
    world.post("/_levers/rate_budget", json={"remaining": 0})
    resp = _post(world, [number])
    assert resp.status_code == 200
    assert resp.json()["errors"][0]["type"] == "RATE_LIMITED"
    assert resp.json()["data"] is None
    assert resp.headers["X-RateLimit-Remaining"] == "0"
    assert int(resp.headers["X-RateLimit-Reset"]) > int(START.timestamp())
