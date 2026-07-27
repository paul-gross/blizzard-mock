"""Fixtures shared by the mock coding-harness test modules."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from blizzard_mock.harness import engine


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
