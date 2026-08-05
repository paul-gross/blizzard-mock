"""The per-env scratch-path convention for a fixture workspace.

Pure path arithmetic — no filesystem access — so a fixture's layout can be
computed without the fixture existing. This module owns *where* a fixture
lives; the service owns *materializing* it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FixtureLayout:
    """The resolved on-disk layout of one per-env fixture workspace: ``origins/``
    (bare git origins), ``workspace/`` (a real winter workspace root), and
    ``fixture.json``, under a per-env scratch path.
    """

    env: str
    root: Path
    workspace: Path
    origins: Path
    manifest: Path

    @classmethod
    def resolve(cls, scratch_root: Path, env: str) -> FixtureLayout:
        """Compute the layout for ``env`` under ``scratch_root`` (no I/O)."""
        root = scratch_root / env
        return cls(
            env=env,
            root=root,
            workspace=root / "workspace",
            origins=root / "origins",
            manifest=root / "fixture.json",
        )

    def origin_path(self, repo: str) -> Path:
        """On-disk path of the bare origin for project repo ``repo``."""
        return self.origins / f"{repo}.git"

    def origin_url(self, repo: str) -> str:
        """The ``file://`` remote URL the workspace addresses ``repo`` by."""
        return f"file://{self.origin_path(repo)}"
