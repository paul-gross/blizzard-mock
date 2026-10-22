"""A bare-repo world and forge client shared by the forge policy test modules."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from blizzard_mock.forge.app import create_app
from blizzard_mock.forge.config import ForgeConfig
from blizzard_mock.forge.domain.clock import FixedClock

REPO = "octocat/hello"
BARE = Path("octocat/hello.git")
START = datetime(2026, 7, 13, tzinfo=UTC)


def _git(*args: str) -> None:
    subprocess.run(["git", *args], check=True, capture_output=True, text=True)


def build_repos(root: Path) -> Path:
    """``main`` (initial + a change), ``ahead`` / ``ahead2`` (one commit on top of the
    current ``main``), ``feature`` (branched before ``main`` moved, no conflict), and
    ``clash`` (conflicts with ``main``)."""
    work = root / "work"
    work.mkdir(parents=True)
    _git("init", "-b", "main", str(work))
    w = ["-C", str(work)]
    _git(*w, "config", "user.email", "seed@t")
    _git(*w, "config", "user.name", "seed")
    (work / "a.txt").write_text("hello\n")
    _git(*w, "add", "-A")
    _git(*w, "commit", "-m", "initial")
    _git(*w, "checkout", "-b", "feature")
    (work / "feature.txt").write_text("feature\n")
    _git(*w, "add", "-A")
    _git(*w, "commit", "-m", "feat")
    _git(*w, "checkout", "-b", "clash", "main")
    (work / "a.txt").write_text("clash\n")
    _git(*w, "commit", "-am", "clash")
    _git(*w, "checkout", "main")
    (work / "a.txt").write_text("main change\n")
    _git(*w, "commit", "-am", "main change")
    for branch in ("ahead", "ahead2"):
        _git(*w, "checkout", "-b", branch, "main")
        (work / f"{branch}.txt").write_text(f"{branch}\n")
        _git(*w, "add", "-A")
        _git(*w, "commit", "-m", branch)
    _git(*w, "checkout", "main")
    bare = root / BARE
    bare.parent.mkdir(parents=True)
    _git("clone", "--bare", str(work), str(bare))
    return root


@contextmanager
def forge(root: Path, clock: FixedClock | None = None) -> Iterator[TestClient]:
    config = ForgeConfig(repos_dir=build_repos(root / "repos"), host="127.0.0.1", port=4421)
    with TestClient(create_app(config, clock=clock or FixedClock(START))) as client:
        yield client


def bare_git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "--git-dir", str(root / "repos" / BARE), *args], check=True, capture_output=True, text=True
    )
    return out.stdout.strip()


def sha_of(client: TestClient, ref: str) -> str:
    return client.get(f"/repos/{REPO}/git/ref/heads/{ref}").json()["object"]["sha"]


def open_pull(client: TestClient, head: str, base: str = "main") -> dict[str, Any]:
    return client.post(f"/repos/{REPO}/pulls", json={"title": head, "head": head, "base": base}).json()
