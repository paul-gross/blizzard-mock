"""The per-env scratch-path convention for a fixture workspace.

Pure path arithmetic — no filesystem access — so the layout of a fixture can be
computed (for ``destroy`` / ``path``) without the fixture existing. The domain
core owns *where* a fixture lives; the service (and its injected adapters) own
*materializing* it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FixtureLayout:
    """The resolved on-disk layout of one per-env fixture workspace.

    A fixture is a self-contained directory tree under a per-env scratch path::

        <scratch_root>/<env>/                 root — the whole disposable fixture
        ├── origins/<repo>.git                bare git origins (the forge's git truth)
        ├── workspace/                         the REAL winter workspace root
        │   ├── .winter/config.toml            declares the origins as project repos
        │   └── tools/winter-cli/              the winter framework, from a local source
        └── fixture.json                       provenance manifest

    ``workspace`` is a genuine winter workspace root (it holds ``.winter/config.toml``
    *and* ``tools/winter-cli/``): the runner under test drives the real ``winter``
    CLI against it. ``origins`` is the single git truth the mock forge fronts.
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
