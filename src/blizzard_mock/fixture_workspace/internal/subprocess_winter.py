"""Run the real winter CLI against a fixture — the ``IWinterCli`` adapter.

Replicates what ``~/.local/bin/winter`` does, but self-contained (no dependency on
the global shim being installed): it invokes the fixture's *own* ``tools/winter-cli``
via ``mise exec -- uv run``, with the process CWD pinned to ``tools/winter-cli``.

Two subtleties this must honor, both encoded in the shim:

- **Root resolution.** CWD is pinned to ``<fixture>/tools/winter-cli`` so winter's own
  root-walk resolves the *fixture*, never the outer workspace the mock itself runs in
  (pinned by ``tests/test_pin_mock.py::
  test_the_winter_cli_subprocess_runs_with_cwd_pinned_to_the_fixtures_own_cli``).
- **mise trust.** A freshly *cloned* ``tools/winter-cli/mise.toml`` is untrusted, so
  ``ensure_ready`` trusts it once before the first ``run``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from pathlib import Path

import structlog

from blizzard_mock.fixture_workspace.errors import FixtureError

log = structlog.get_logger(__name__)


class SubprocessWinterCli:
    """``IWinterCli`` over the fixture's own ``tools/winter-cli`` (mise + uv)."""

    def ensure_ready(self, workspace: Path) -> None:
        cli = self._cli_dir(workspace)
        # Trust the freshly cloned mise config so `mise exec` will run it.
        self._run(["mise", "trust", "--quiet", str(cli / "mise.toml")], cwd=cli, what="mise trust")
        log.debug("fixture.winter.ready", workspace=str(workspace))

    def run(self, workspace: Path, args: Sequence[str]) -> None:
        cli = self._cli_dir(workspace)
        cmd = ["mise", "-C", str(cli), "exec", "--", "uv", "run", "--project", str(cli), "winter", *args]
        self._run(cmd, cwd=cli, what=f"winter {' '.join(args)}")
        log.info("fixture.winter.run", workspace=str(workspace), args=list(args))

    @staticmethod
    def _cli_dir(workspace: Path) -> Path:
        cli = workspace / "tools" / "winter-cli"
        if not cli.is_dir():
            raise FixtureError(f"no tools/winter-cli under fixture workspace {workspace}")
        return cli

    @staticmethod
    def _run(cmd: list[str], *, cwd: Path, what: str) -> None:
        result = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip()[-2000:]
            raise FixtureError(f"{what} failed ({result.returncode}): {tail}")
