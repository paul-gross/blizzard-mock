"""Repositories created on demand through ``POST /_repos`` (``blizzard-mock:unit-test``)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.forge_world import forge


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with forge(tmp_path) as c:
        yield c


def test_create_answers_201_and_serves_the_seed(client: TestClient, tmp_path: Path) -> None:
    resp = client.post("/_repos", json={"owner": "blizzard", "name": "t-one"})
    assert resp.status_code == 201
    assert resp.json() == {
        "full_name": "blizzard/t-one",
        "origin": str(tmp_path / "repos" / "blizzard" / "t-one.git"),
        "default_branch": "main",
    }
    assert client.get("/repos/blizzard/t-one").json()["default_branch"] == "main"
    assert client.get("/repos/blizzard/t-one/git/ref/heads/main").status_code == 200


def test_seed_files_are_on_main(client: TestClient, tmp_path: Path) -> None:
    client.post("/_repos", json={"owner": "blizzard", "name": "t-two", "seed": "toy-web"})
    git_dir = tmp_path / "repos" / "blizzard" / "t-two.git"
    out = subprocess.run(
        ["git", "--git-dir", str(git_dir), "ls-tree", "-r", "--name-only", "main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert out == ["README.md", "src/index.js"]


def test_second_create_conflicts(client: TestClient) -> None:
    assert client.post("/_repos", json={"owner": "blizzard", "name": "dup"}).status_code == 201
    resp = client.post("/_repos", json={"owner": "blizzard", "name": "dup"})
    assert resp.status_code == 409
    assert resp.json()["message"] == "repository exists"


@pytest.mark.parametrize(
    "body",
    [
        {"owner": "bad owner", "name": "x"},
        {"owner": "-lead", "name": "x"},
        {"owner": "blizzard", "name": "has space"},
        {"owner": "blizzard", "name": ".."},
        {"owner": "blizzard", "name": "x.git"},
        {"owner": "blizzard", "name": "x", "seed": "nope"},
    ],
)
def test_invalid_input_answers_422(client: TestClient, body: dict[str, str]) -> None:
    assert client.post("/_repos", json=body).status_code == 422


def test_created_repo_starts_empty(client: TestClient) -> None:
    client.post("/_repos", json={"owner": "blizzard", "name": "empty"})
    for path in ("issues", "pulls", "labels"):
        resp = client.get(f"/repos/blizzard/empty/{path}")
        assert resp.status_code == 200
        assert resp.json() == []
