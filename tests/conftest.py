"""Fixtures shared by the mock coding-harness test modules."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from blizzard_mock.harness import engine
from tests.opencode2_service import Service, Starter


@pytest.fixture
def fenced_repo(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """A git worktree with the fence marker dropped and a fenced env dict."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.invalid"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=tmp_path, check=True)
    (tmp_path / "seed.txt").write_text("seed\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=tmp_path, check=True)
    engine.write_fence_marker(tmp_path)
    env = engine.fenced_env({"PATH": os.environ.get("PATH", "")})
    return tmp_path, env


@pytest.fixture
def start_service(tmp_path: Path) -> Iterator[Starter]:
    """Start emitted OpenCode 2 artifacts' services under isolated homes; every one is stopped on teardown."""
    started: list[Service] = []

    def start(binary: Path, home: Path | None = None, *, extra_env: Mapping[str, str] | None = None) -> Service:
        service = Service(binary, home or tmp_path / "home", extra_env)
        started.append(service)
        return service

    yield start
    for service in started:
        service.cli("service", "stop")
