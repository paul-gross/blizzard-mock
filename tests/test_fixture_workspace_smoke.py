"""Coverage for the fixture-workspace scaffold.

Two tiers:

* **Unit** — real git (bare origins are cheap and deterministic) driving the
  service against a *fake* winter CLI and a hand-built fake winter source, so the
  minting structure is asserted without the slow real ``winter ws init``.
* **Component** — the genuine end-to-end: real git + real ``winter ws init`` +
  real env creation against a local winter source. Slow and environment-bound, so
  it is skipped unless a winter source is discoverable (``$BLIZZARD_MOCK_WINTER_SOURCE``
  or an enclosing winter workspace).
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard_mock.fixture_workspace import cli
from blizzard_mock.fixture_workspace.config import render_config_toml
from blizzard_mock.fixture_workspace.errors import FixtureError
from blizzard_mock.fixture_workspace.internal.subprocess_git import SubprocessGit
from blizzard_mock.fixture_workspace.scratch import FixtureLayout
from blizzard_mock.fixture_workspace.service import FixtureWorkspaceService

# --------------------------------------------------------------------------- #
# test doubles / helpers
# --------------------------------------------------------------------------- #


class FakeWinterCli:
    """Records winter invocations and simulates ``ws init`` cheaply."""

    def __init__(self) -> None:
        self.ready: list[Path] = []
        self.runs: list[tuple[Path, list[str]]] = []

    def ensure_ready(self, workspace: Path) -> None:
        self.ready.append(workspace)

    def run(self, workspace: Path, args: Sequence[str]) -> None:
        self.runs.append((workspace, list(args)))
        if list(args) == ["ws", "init"]:
            (workspace / "projects").mkdir(parents=True, exist_ok=True)


def _make_winter_source(root: Path) -> Path:
    """A minimal *committed* stand-in for a winter workspace: has tools/winter-cli + .winter/config.toml."""
    source = root / "winter-source"
    (source / "tools" / "winter-cli").mkdir(parents=True)
    (source / "tools" / "winter-cli" / "pyproject.toml").write_text("[project]\nname='winter-cli'\n")
    (source / ".winter").mkdir()
    (source / ".winter" / "config.toml").write_text('main_branch = "master"\n')
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "--initial-branch=master", str(source)], check=True)
    subprocess.run(["git", "-C", str(source), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-q", "-m", "seed"], check=True, env=env)
    return source


def _service(tmp_path: Path) -> tuple[FixtureWorkspaceService, FakeWinterCli]:
    winter = FakeWinterCli()
    svc = FixtureWorkspaceService(
        git=SubprocessGit(),
        winter=winter,
        scratch_root=tmp_path / "scratch",
        winter_source=_make_winter_source(tmp_path),
    )
    return svc, winter


# --------------------------------------------------------------------------- #
# pure units
# --------------------------------------------------------------------------- #


def test_scratch_layout_convention() -> None:
    layout = FixtureLayout.resolve(Path("/s"), "alpha")
    assert layout.root == Path("/s/alpha")
    assert layout.workspace == Path("/s/alpha/workspace")
    assert layout.origins == Path("/s/alpha/origins")
    assert layout.origin_path("toy-api") == Path("/s/alpha/origins/toy-api.git")
    assert layout.origin_url("toy-api") == "file:///s/alpha/origins/toy-api.git"


def test_render_config_toml_declares_file_origins() -> None:
    text = render_config_toml([("toy-api", "file:///x/toy-api.git")])
    assert 'main_branch = "main"' in text
    assert "[[project_repository]]" in text
    assert 'name = "toy-api"' in text
    assert 'url = "file:///x/toy-api.git"' in text


# --------------------------------------------------------------------------- #
# unit: minting structure (real git, fake winter)
# --------------------------------------------------------------------------- #


def test_mint_materializes_bare_origins_and_workspace(tmp_path: Path) -> None:
    svc, winter = _service(tmp_path)
    layout = svc.mint("alpha")

    # Bare origins exist and carry seed history.
    for repo in ("toy-api", "toy-web"):
        bare = layout.origin_path(repo)
        assert bare.is_dir()
        log = subprocess.run(
            ["git", "--git-dir", str(bare), "log", "--oneline"], capture_output=True, text=True, check=True
        )
        assert f"seed {repo}" in log.stdout

    # The winter workspace materialized as a real root with our generated config.
    config = (layout.workspace / ".winter" / "config.toml").read_text()
    assert f"file://{layout.origin_path('toy-api')}" in config
    assert (layout.workspace / "tools" / "winter-cli").is_dir()

    # winter was driven: prepared once, then `ws init`.
    assert winter.ready == [layout.workspace]
    assert winter.runs == [(layout.workspace, ["ws", "init"])]

    # Manifest records provenance for the forge + downstream tooling.
    manifest = json.loads(layout.manifest.read_text())
    assert manifest["env"] == "alpha"
    assert {r["name"] for r in manifest["repos"]} == {"toy-api", "toy-web"}
    assert manifest["origins"] == str(layout.origins)


def test_mint_refuses_when_fixture_exists(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)
    svc.mint("alpha")
    with pytest.raises(FixtureError, match="already exists"):
        svc.mint("alpha")


def test_destroy_removes_fixture_and_is_idempotent(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)
    layout = svc.mint("alpha")
    assert svc.destroy("alpha") is True
    assert not layout.root.exists()
    assert svc.destroy("alpha") is False


def test_reset_remints_from_clean(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)
    layout = svc.mint("alpha")
    stray = layout.root / "stray.txt"
    stray.write_text("dirty")
    svc.reset("alpha")
    assert not stray.exists()
    assert (layout.workspace / ".winter" / "config.toml").is_file()


def test_two_envs_never_share_a_fixture(tmp_path: Path) -> None:
    svc, _ = _service(tmp_path)
    a = svc.mint("alpha")
    b = svc.mint("beta")
    assert a.root != b.root
    assert a.root.is_dir() and b.root.is_dir()


# --------------------------------------------------------------------------- #
# CLI surface
# --------------------------------------------------------------------------- #


def test_cli_path_prints_workspace_and_origins(tmp_path: Path) -> None:
    runner = CliRunner()
    common = ["--env", "alpha", "--scratch-root", str(tmp_path / "s")]
    ws = runner.invoke(cli.main, ["path", *common])
    origins = runner.invoke(cli.main, ["path", *common, "--part", "origins"])
    assert ws.exit_code == 0
    assert ws.output.strip().endswith("/alpha/workspace")
    assert origins.output.strip().endswith("/alpha/origins")


def test_cli_help_lists_verbs() -> None:
    result = CliRunner().invoke(cli.main, ["--help"])
    assert result.exit_code == 0
    for verb in ("mint", "destroy", "reset", "path"):
        assert verb in result.output


# --------------------------------------------------------------------------- #
# component: real winter, end-to-end (skipped without a winter source)
# --------------------------------------------------------------------------- #


def _discoverable_winter_source() -> Path | None:
    from blizzard_mock.fixture_workspace.cli import _resolve_winter_source

    return _resolve_winter_source(None)


def test_real_winter_ws_init_and_env_creation(tmp_path: Path) -> None:
    source = _discoverable_winter_source()
    if source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    from blizzard_mock.fixture_workspace.internal.subprocess_winter import SubprocessWinterCli

    svc = FixtureWorkspaceService(
        git=SubprocessGit(),
        winter=SubprocessWinterCli(),
        scratch_root=tmp_path / "scratch",
        winter_source=source,
    )
    layout = svc.mint("alpha")

    # ws init cloned the toy project repos into projects/.
    for repo in ("toy-api", "toy-web"):
        assert (layout.workspace / "projects" / repo / ".git").exists()

    # The real CLI can create a feature env inside the fixture (the runner's acquire path).
    SubprocessWinterCli().run(layout.workspace, ["ws", "init", "alpha"])
    for repo in ("toy-api", "toy-web"):
        assert (layout.workspace / "alpha" / repo).is_dir()
