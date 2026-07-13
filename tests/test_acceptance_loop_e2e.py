"""End-to-end acceptance proof for the blizzard-mock fleet (the P4 exit criterion).

The bootstrap plan's Phase 4 exit criterion
(``blizzard-discovery:/implementation/bootstrap.md``):

    a scripted prompt, run through the mock harness in a fixture-workspace env,
    lands a commit that the mock forge merges to bare ``main`` — no blizzard code
    involved yet.

This test is that criterion turned into a committed, repeatable artifact. It wires
all the mock components together over their *real* seams — the forge is driven over
its HTTP surface, the harness over its façade CLI, git over real ``file://`` pushes
and merges — with no in-process shortcuts:

1. mint a real, disposable **fixture workspace** (bare ``file://`` origins plus a
   real winter workspace) with the fixture-workspace scaffold;
2. create a feature env inside it — the runner's acquire path;
3. start the real **mock GitHub forge** (its ``blizzard-mock-forge`` uvicorn
   entrypoint) fronting the fixture's bare origins — the single git truth;
4. file an **issue** over the forge's HTTP API — the work-source seam;
5. run the **mock Claude Code façade** binary in the fixture worktree with a
   scripted prompt that applies a diff and makes a real commit — the prompt is
   the program;
6. **push** the branch to the ``file://`` origin;
7. open a **PR** at the forge and **merge** it over the API — the delivery seam;
8. assert the commit is **reachable from the bare repo's master** at both ends:
   git ancestry in the bare origin, and the forge's merged-check (``204``).

Slow and environment-bound (a real ``winter ws init``, a real forge subprocess),
so it is marked ``e2e`` and skipped when no local winter source is discoverable
(``$BLIZZARD_MOCK_WINTER_SOURCE`` or an enclosing winter workspace).

Reproduce with::

    uv run pytest -m e2e
"""

from __future__ import annotations

import contextlib
import json
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from blizzard_mock.fixture_workspace.cli import _resolve_winter_source
from blizzard_mock.fixture_workspace.internal.subprocess_git import SubprocessGit
from blizzard_mock.fixture_workspace.internal.subprocess_winter import SubprocessWinterCli
from blizzard_mock.fixture_workspace.scratch import FixtureLayout
from blizzard_mock.fixture_workspace.service import FixtureWorkspaceService
from blizzard_mock.harness import engine

pytestmark = pytest.mark.e2e

# The fixture project repo the loop drives, and the owner the forge sees it under.
# Repo resolution is permissive (``forge/internal/git_backend.py``): ``owner/name``
# accepts ``<dir>/name.git``, so the flat ``origins/toy-api.git`` the fixture mints
# backs any owner — ``blizzard`` here.
OWNER = "blizzard"
REPO_NAME = "toy-api"
REPO = f"{OWNER}/{REPO_NAME}"
# The feature env created *inside* the fixture (where the mock agent works).
FIXTURE_ENV = "work"

# The change the mock agent "authors": a new file, as a unified diff its
# ``apply_diff`` helper applies for real before it commits.
_LANDED_DIFF = (
    "diff --git a/landed.txt b/landed.txt\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/landed.txt\n"
    "@@ -0,0 +1 @@\n"
    "+landed by the mock harness\n"
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def _is_ancestor(bare: Path, ancestor: str, branch: str) -> bool:
    return (
        subprocess.run(["git", "--git-dir", str(bare), "merge-base", "--is-ancestor", ancestor, branch]).returncode == 0
    )


@contextlib.contextmanager
def _forge(repos_dir: Path, port: int) -> Iterator[httpx.Client]:
    """Start the real ``blizzard-mock-forge`` uvicorn entrypoint over ``repos_dir``."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "blizzard_mock.forge.cli",
            "--repos-dir",
            str(repos_dir),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=10.0)
    try:
        _await_healthz(proc, client)
        yield client
    finally:
        client.close()
        proc.terminate()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        if proc.poll() is None:
            proc.kill()


def _await_healthz(proc: subprocess.Popen[str], client: httpx.Client, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise AssertionError(f"forge exited early ({proc.returncode}):\n{out}")
        with contextlib.suppress(httpx.HTTPError):
            if client.get("/healthz").status_code == 200:
                return
        time.sleep(0.1)
    raise AssertionError("forge did not become healthy within timeout")


def _run_mock_agent(worktree: Path, fenced_env: dict[str, str]) -> dict[str, object]:
    """Drive the mock Claude Code façade in ``worktree``; return its result envelope."""
    script = f"apply_diff({_LANDED_DIFF!r})\nsha = commit('feat: mock harness lands a change')\nverdict('done', f'committed {{sha}}')\n"
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "blizzard_mock.harness.facades.claude_code",
            "-p",
            "--output-format",
            "json",
            script,
        ],
        cwd=worktree,
        env=fenced_env,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, f"mock agent failed: {proc.stderr}\n{proc.stdout}"
    return json.loads(proc.stdout)


def test_acceptance_loop_lands_a_mock_commit_the_forge_merges(tmp_path: Path) -> None:
    winter_source = _resolve_winter_source(None)
    if winter_source is None:
        pytest.skip("no local winter source (set BLIZZARD_MOCK_WINTER_SOURCE)")

    # 1. Mint a real, disposable fixture workspace: bare file:// origins + a real
    #    winter workspace whose `winter ws init` clones the toy repos into projects/.
    svc = FixtureWorkspaceService(
        git=SubprocessGit(),
        winter=SubprocessWinterCli(),
        scratch_root=tmp_path / "scratch",
        winter_source=winter_source,
    )
    layout: FixtureLayout = svc.mint("e2e")

    # 2. Create a feature env inside the fixture — the runner's acquire path. The
    #    toy-api worktree lands on branch `work`, based on the origin's master.
    SubprocessWinterCli().run(layout.workspace, ["ws", "init", FIXTURE_ENV])
    worktree = layout.workspace / FIXTURE_ENV / REPO_NAME
    assert worktree.is_dir(), "winter ws init did not create the fixture feature-env worktree"

    # Fence the fixture tree so the mock harness will run (arbitrary code execution
    # is the feature, and it is gated on a marker + env var). Marker at the fixture
    # workspace root covers every worktree under it via the ancestor walk.
    engine.write_fence_marker(layout.workspace)
    fenced_env = engine.fenced_env({"PATH": __import__("os").environ.get("PATH", "")})

    origin_bare = layout.origin_path(REPO_NAME)
    port = _free_port()

    # 3. Start the real forge fronting the fixture's bare origins (single git truth).
    with _forge(layout.origins, port) as forge:
        # Sanity: the forge sees the fixture's bare repo, default branch master.
        repo = forge.get(f"/repos/{REPO}")
        assert repo.status_code == 200, repo.text
        assert repo.json()["default_branch"] == "master"

        # 4. File an issue over the HTTP API — the work-source seam.
        issue = forge.post(
            f"/repos/{REPO}/issues",
            json={"title": "land a change", "body": "the acceptance-loop chunk"},
        )
        assert issue.status_code == 201, issue.text
        issue_number = issue.json()["number"]
        assert forge.get(f"/repos/{REPO}/issues/{issue_number}").json()["state"] == "open"

        # 5. Run the mock Claude Code façade in the worktree — the prompt is the
        #    program: it applies the diff and makes a real commit.
        envelope = _run_mock_agent(worktree, fenced_env)
        assert envelope["is_error"] is False, envelope
        assert "<Choice>done</Choice>" in str(envelope["result"])
        assert (worktree / "landed.txt").read_text() == "landed by the mock harness\n"
        head_sha = _git("-C", str(worktree), "rev-parse", "HEAD").strip()

        # 6. Push the branch to the file:// origin the forge fronts.
        _git("-C", str(worktree), "push", "origin", f"{FIXTURE_ENV}:{FIXTURE_ENV}")
        assert _git("--git-dir", str(origin_bare), "rev-parse", f"refs/heads/{FIXTURE_ENV}").strip() == head_sha

        # 7. Open a PR and merge it over the API — the delivery seam. Mergeability
        #    is computed against the real refs just pushed.
        pull = forge.post(
            f"/repos/{REPO}/pulls",
            json={"title": "land a change", "head": FIXTURE_ENV, "base": "master", "body": f"closes #{issue_number}"},
        )
        assert pull.status_code == 201, pull.text
        pull_view = pull.json()
        assert pull_view["mergeable"] is True
        assert pull_view["head"]["sha"] == head_sha
        number = pull_view["number"]

        merged = forge.put(f"/repos/{REPO}/pulls/{number}/merge", json={})
        assert merged.status_code == 200, merged.text
        assert merged.json()["merged"] is True
        merge_sha = merged.json()["sha"]

        # 8. Assert the outcome at both ends: the forge reports the PR merged, and
        #    the mock agent's commit is reachable from the bare repo's master.
        assert forge.get(f"/repos/{REPO}/pulls/{number}/merge").status_code == 204
        assert forge.get(f"/repos/{REPO}/pulls/{number}").json()["merged"] is True

    assert _is_ancestor(origin_bare, head_sha, "refs/heads/master"), "mock commit not reachable from bare master"
    assert _is_ancestor(origin_bare, merge_sha, "refs/heads/master"), "merge commit not on bare master"
