"""Branch merges, ref create/delete, and every merge method (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.forge_world import REPO, bare_git, forge, open_pull, sha_of


@pytest.fixture
def world(tmp_path: Path) -> Iterator[tuple[TestClient, Path]]:
    with forge(tmp_path) as client:
        yield client, tmp_path


def test_merge_branches_201_204_and_409(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    main = sha_of(client, "main")
    merged = client.post(f"/repos/{REPO}/merges", json={"base": "main", "head": "ahead"})
    assert merged.status_code == 201
    body = merged.json()
    assert body["parents"][0]["sha"] == main
    assert sha_of(client, "main") == body["sha"]

    again = client.post(f"/repos/{REPO}/merges", json={"base": "main", "head": "ahead"})
    assert again.status_code == 204
    assert sha_of(client, "main") == body["sha"]

    conflict = client.post(f"/repos/{REPO}/merges", json={"base": "main", "head": "clash"})
    assert conflict.status_code == 409
    assert sha_of(client, "main") == body["sha"]


def test_merge_branches_missing_side_is_404(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    assert client.post(f"/repos/{REPO}/merges", json={"base": "ghost", "head": "ahead"}).status_code == 404
    assert client.post(f"/repos/{REPO}/merges", json={"base": "main", "head": "ghost"}).status_code == 404


def test_ref_created_twice_then_deleted_twice(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    main = sha_of(client, "main")
    created = client.post(f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/probe", "sha": main})
    assert created.status_code == 201
    assert created.json()["ref"] == "refs/heads/probe"
    assert sha_of(client, "probe") == main

    again = client.post(f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/probe", "sha": main})
    assert again.status_code == 422
    assert again.json()["message"] == "Reference already exists"

    assert client.delete(f"/repos/{REPO}/git/refs/heads/probe").status_code == 204
    second = client.delete(f"/repos/{REPO}/git/refs/heads/probe")
    assert second.status_code == 422
    assert second.json()["message"] == "Reference does not exist"


def test_ref_create_rejects_unknown_sha_and_bare_names(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    assert client.post(f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/x", "sha": "0" * 40}).status_code == 422
    assert client.post(f"/repos/{REPO}/git/refs", json={"ref": "x", "sha": sha_of(client, "main")}).status_code == 422


def test_squash_merge_lands_one_commit_on_the_base_tip(world: tuple[TestClient, Path]) -> None:
    client, root = world
    main = sha_of(client, "main")
    pull = open_pull(client, "feature")
    resp = client.put(f"/repos/{REPO}/pulls/{pull['number']}/merge", json={"merge_method": "squash"})
    assert resp.status_code == 200
    landed = resp.json()["sha"]
    assert bare_git(root, "rev-parse", "main") == landed
    assert bare_git(root, "rev-list", "--parents", "-n", "1", landed).split() == [landed, main]
    assert landed != pull["head"]["sha"]


def test_external_squash_merge_is_neither_head_nor_merge_of_it(world: tuple[TestClient, Path]) -> None:
    client, root = world
    main = sha_of(client, "main")
    pull = open_pull(client, "feature")
    client.post("/_levers/externally_merged", json={"repo": REPO, "number": pull["number"], "method": "squash"})
    landed = sha_of(client, "main")
    assert landed not in (pull["head"]["sha"], main)
    assert bare_git(root, "rev-list", "--parents", "-n", "1", landed).split() == [landed, main]
    assert client.get(f"/repos/{REPO}/pulls/{pull['number']}").json()["merged"] is True


def test_external_rebase_merge_replays_commits(world: tuple[TestClient, Path]) -> None:
    client, root = world
    pull = open_pull(client, "feature")
    client.post("/_levers/externally_merged", json={"repo": REPO, "number": pull["number"], "method": "rebase"})
    landed = sha_of(client, "main")
    assert landed != pull["head"]["sha"]
    assert len(bare_git(root, "rev-list", "--parents", "-n", "1", landed).split()) == 2  # linear, no merge commit


def test_rebase_merge_of_an_up_to_date_head_still_rewrites(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    pull = open_pull(client, "ahead")
    resp = client.put(f"/repos/{REPO}/pulls/{pull['number']}/merge", json={"merge_method": "rebase"})
    assert resp.status_code == 200
    assert resp.json()["sha"] != pull["head"]["sha"]
    assert sha_of(client, "main") == resp.json()["sha"]


def test_closed_pull_with_deleted_head_still_lists(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    pull = open_pull(client, "ahead")
    head = pull["head"]["sha"]
    client.patch(f"/repos/{REPO}/pulls/{pull['number']}", json={"state": "closed"})
    assert client.delete(f"/repos/{REPO}/git/refs/heads/ahead").status_code == 204
    listed = client.get(f"/repos/{REPO}/pulls", params={"state": "all"})
    assert listed.status_code == 200
    assert [p["head"]["sha"] for p in listed.json()] == [head]


def test_deleting_the_head_of_an_open_pull_closes_it(world: tuple[TestClient, Path]) -> None:
    client, _ = world
    pull = open_pull(client, "ahead")
    client.delete(f"/repos/{REPO}/git/refs/heads/ahead")
    got = client.get(f"/repos/{REPO}/pulls/{pull['number']}").json()
    assert got["state"] == "closed"
    assert got["head"]["sha"] == pull["head"]["sha"]
    assert client.get(f"/repos/{REPO}/pulls").json() == []
    reopen = client.patch(f"/repos/{REPO}/pulls/{pull['number']}", json={"state": "open"})
    assert reopen.status_code == 422
