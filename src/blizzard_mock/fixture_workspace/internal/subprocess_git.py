"""Real git plumbing for the fixture — the ``IGit`` adapter.

Drives the ``git`` binary via ``subprocess``. Seed commits use a fixed, injected
identity passed through the environment so minting never depends on (or mutates)
the machine's global git config, and produces byte-stable history.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

import structlog

from blizzard_mock.fixture_workspace.errors import FixtureError

log = structlog.get_logger(__name__)

# Deterministic identity for seed history — the toy repos' committed origin.
_SEED_NAME = "Blizzard Mock"
_SEED_EMAIL = "mock@blizzard.invalid"


class SubprocessGit:
    """``IGit`` over the local ``git`` binary."""

    def init_bare(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(["init", "--bare", "--initial-branch=main", str(path)])
        log.debug("fixture.git.init_bare", path=str(path))

    def seed_repo(self, bare: Path, files: Mapping[str, str], message: str, branch: str = "main") -> None:
        with tempfile.TemporaryDirectory(prefix="blizzard-mock-seed-") as tmp:
            work = Path(tmp)
            self._git(["init", f"--initial-branch={branch}", str(work)])
            for rel, content in files.items():
                target = work / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            self._git(["-C", str(work), "add", "-A"])
            self._git(["-C", str(work), "commit", "-m", message], identity=True)
            self._git(["-C", str(work), "remote", "add", "origin", f"file://{bare}"])
            self._git(["-C", str(work), "push", "origin", f"{branch}:{branch}"])
        log.debug("fixture.git.seed_repo", bare=str(bare), files=len(files))

    def clone_local(self, source: Path, dest: Path) -> None:
        # --local clones committed state via cheap hardlinks; a disposable fixture
        # never mutates the shared objects, so hardlinks are safe here.
        self._git(["clone", "--local", "--quiet", str(source), str(dest)])
        log.debug("fixture.git.clone_local", source=str(source), dest=str(dest))

    def _git(self, args: list[str], *, identity: bool = False) -> None:
        env = None
        if identity:
            env = {
                "GIT_AUTHOR_NAME": _SEED_NAME,
                "GIT_AUTHOR_EMAIL": _SEED_EMAIL,
                "GIT_COMMITTER_NAME": _SEED_NAME,
                "GIT_COMMITTER_EMAIL": _SEED_EMAIL,
            }
        merged = {**os.environ, **env} if env else None
        result = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            env=merged,
        )
        if result.returncode != 0:
            raise FixtureError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
