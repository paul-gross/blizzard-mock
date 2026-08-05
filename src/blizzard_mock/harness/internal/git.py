"""Real git plumbing for the behavior-script helpers.

``commit`` makes an actual commit in the acquired worktree and ``apply_diff``
mutates real files. Thin subprocess wrappers over ``git`` — no gitpython
object graph needed for these two operations.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from pathlib import Path


class GitError(RuntimeError):
    """A git plumbing command exited non-zero — surfaced to the behavior script."""


def _run(args: list[str], *, cwd: Path, env: Mapping[str, str], input_text: str | None = None) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=dict(env),
        input=input_text,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def apply_diff(diff: str, *, cwd: Path, env: Mapping[str, str]) -> None:
    """Apply a unified ``diff`` to the worktree at ``cwd`` via ``git apply``."""
    text = diff if diff.endswith("\n") else diff + "\n"
    _run(["apply", "--whitespace=nowarn", "-"], cwd=cwd, env=env, input_text=text)


def commit(message: str, *, cwd: Path, env: Mapping[str, str]) -> str:
    """Stage all changes and make a real commit; return the new commit sha.

    Identity falls back to a deterministic mock author when the environment
    supplies none, so a bare fixture worktree can still commit.
    """
    commit_env = {
        "GIT_AUTHOR_NAME": "Mock Harness",
        "GIT_AUTHOR_EMAIL": "mock-harness@blizzard.invalid",
        "GIT_COMMITTER_NAME": "Mock Harness",
        "GIT_COMMITTER_EMAIL": "mock-harness@blizzard.invalid",
        **env,
    }
    _run(["add", "-A"], cwd=cwd, env=commit_env)
    _run(["commit", "-m", message], cwd=cwd, env=commit_env)
    return _run(["rev-parse", "HEAD"], cwd=cwd, env=commit_env).strip()
