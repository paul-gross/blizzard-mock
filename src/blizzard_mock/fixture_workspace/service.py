"""The fixture-workspace minting service — the domain core.

Owns the *what* and *order* of minting a fixture; the *how* of touching git,
running winter, and reading the wall clock is inverted behind Protocol seams
(``bzh:dependency-inversion``) implemented under ``internal/`` and injected at the
composition root (the CLI). The service imports no ``subprocess``, ``httpx``, or
``click`` — only its own domain modules and the standard library — so it is unit
testable with a real git adapter and a fake winter runner.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Protocol

from blizzard_mock.fixture_workspace.config import render_config_toml
from blizzard_mock.fixture_workspace.errors import FixtureError
from blizzard_mock.fixture_workspace.scratch import FixtureLayout
from blizzard_mock.fixture_workspace.seed import TOY_REPOS, RepoSeed


class IGit(Protocol):
    """Git plumbing the fixture needs — implemented by ``internal/subprocess_git.py``."""

    def init_bare(self, path: Path) -> None:
        """Create a bare repository at ``path`` (the ``file://`` origin)."""
        ...

    def seed_repo(self, bare: Path, files: Mapping[str, str], message: str, branch: str = "master") -> None:
        """Commit ``files`` as the initial history of bare repo ``bare`` on ``branch``."""
        ...

    def clone_local(self, source: Path, dest: Path) -> None:
        """Clone the committed state of local repo ``source`` to ``dest`` (no network)."""
        ...


class IWinterCli(Protocol):
    """The real winter CLI, run against the fixture — ``internal/subprocess_winter.py``."""

    def ensure_ready(self, workspace: Path) -> None:
        """One-time preparation so ``run`` works against a freshly cloned framework."""
        ...

    def run(self, workspace: Path, args: Sequence[str]) -> None:
        """Run ``winter <args>`` with ``workspace`` as the resolved workspace root."""
        ...


class FixtureWorkspaceService:
    """Mints, tears down, and locates per-env fixture workspaces."""

    def __init__(
        self,
        *,
        git: IGit,
        winter: IWinterCli,
        scratch_root: Path,
        winter_source: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        repos: Sequence[RepoSeed] = TOY_REPOS,
    ) -> None:
        self._git = git
        self._winter = winter
        self._scratch_root = scratch_root
        self._winter_source = winter_source
        self._now = clock if clock is not None else datetime.now
        self._repos = tuple(repos)

    def layout(self, env: str) -> FixtureLayout:
        """The resolved (not necessarily materialized) layout for ``env``."""
        return FixtureLayout.resolve(self._scratch_root, env)

    def exists(self, env: str) -> bool:
        """Whether a fixture for ``env`` is materialized on disk."""
        return self.layout(env).root.exists()

    def mint(self, env: str) -> FixtureLayout:
        """Mint a fresh fixture workspace for ``env``. Refuses if one already exists."""
        layout = self.layout(env)
        if layout.root.exists():
            raise FixtureError(
                f"fixture for env {env!r} already exists at {layout.root} — "
                f"use `reset` to re-mint from clean, or `destroy` first"
            )
        if self._winter_source is None:
            raise FixtureError(
                "no winter source configured — pass --winter-source, set "
                "BLIZZARD_MOCK_WINTER_SOURCE, or run from inside a winter workspace"
            )
        if not (self._winter_source / "tools" / "winter-cli").is_dir():
            raise FixtureError(
                f"winter source {self._winter_source} has no tools/winter-cli — "
                f"expected a local winter workspace whose committed master ships the CLI"
            )

        layout.origins.mkdir(parents=True, exist_ok=True)
        for repo in self._repos:
            bare = layout.origin_path(repo.name)
            self._git.init_bare(bare)
            self._git.seed_repo(bare, repo.files, repo.message)

        # The winter framework comes from a LOCAL source's committed master — no
        # network. Cloning the whole workspace yields tools/winter-cli plus a real
        # workspace skeleton; we then replace its config with our own.
        self._git.clone_local(self._winter_source, layout.workspace)
        self._write_config(layout)

        # Drive the real winter CLI against the fixture: clone the toy project
        # repos from their file:// origins into projects/.
        self._winter.ensure_ready(layout.workspace)
        self._winter.run(layout.workspace, ["ws", "init"])

        self._write_manifest(layout)
        return layout

    def destroy(self, env: str) -> bool:
        """Remove the fixture for ``env``. Returns whether anything was removed."""
        layout = self.layout(env)
        if not layout.root.exists():
            return False
        # Safety: only ever delete inside the configured scratch root.
        root = layout.root.resolve()
        base = self._scratch_root.resolve()
        if base != root and base not in root.parents:
            raise FixtureError(f"refusing to destroy {root}: not under scratch root {base}")
        shutil.rmtree(root)
        return True

    def reset(self, env: str) -> FixtureLayout:
        """Re-mint ``env`` from clean: destroy any existing fixture, then mint."""
        self.destroy(env)
        return self.mint(env)

    def _write_config(self, layout: FixtureLayout) -> None:
        config = layout.workspace / ".winter" / "config.toml"
        config.parent.mkdir(parents=True, exist_ok=True)
        repos = [(repo.name, layout.origin_url(repo.name)) for repo in self._repos]
        config.write_text(render_config_toml(repos))
        # A cloned framework may carry a config.lock pinning standalone repos the
        # fixture config no longer declares; drop it so `winter ws init` re-resolves.
        lock = layout.workspace / ".winter" / "config.lock"
        if lock.exists():
            lock.unlink()

    def _write_manifest(self, layout: FixtureLayout) -> None:
        manifest = {
            "env": layout.env,
            "created_at": self._now().isoformat(),
            "root": str(layout.root),
            "workspace": str(layout.workspace),
            "origins": str(layout.origins),
            "winter_source": str(self._winter_source),
            "repos": [
                {
                    "name": repo.name,
                    "origin": str(layout.origin_path(repo.name)),
                    "url": layout.origin_url(repo.name),
                }
                for repo in self._repos
            ],
        }
        layout.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
