"""Compare statuses, merge-commit ranges and parents against a bare repo."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import structlog

from blizzard_mock.forge.domain.errors import BranchNotFound
from blizzard_mock.forge.domain.models import Repo
from blizzard_mock.forge.internal.errors import GitErrorFactory
from blizzard_mock.forge.internal.git_backend import GitBackend

REPO = Repo(owner="octocat", name="hello", default_branch="main")
_RENAMED_BODY = "".join(f"line {n}\n" for n in range(20))


def _git(work: Path, *args: str) -> str:
    out = subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _commit(work: Path, message: str) -> str:
    _git(work, "add", "-A")
    _git(work, "commit", "-m", message)
    return _git(work, "rev-parse", "HEAD")


@pytest.fixture
def shas(tmp_path: Path) -> dict[str, str]:
    work = tmp_path / "work"
    subprocess.run(["git", "init", "-b", "main", str(work)], check=True, capture_output=True)
    _git(work, "config", "user.email", "seed@t")
    _git(work, "config", "user.name", "seed")
    (work / "a.txt").write_text("hello\n")
    (work / "r.txt").write_text(_RENAMED_BODY)
    (work / "gone.txt").write_text("bye\n")
    c0 = _commit(work, "initial")
    _git(work, "branch", "root")

    _git(work, "checkout", "-b", "feature/topic")
    (work / "new.txt").write_text("new\n")
    (work / "r.txt").rename(work / "moved.txt")
    (work / "gone.txt").unlink()
    t1 = _commit(work, "topic")

    _git(work, "checkout", "main")
    (work / "a.txt").write_text("main change\n")
    m1 = _commit(work, "main change")

    _git(work, "checkout", "-b", "feature/merged", c0)
    (work / "b.txt").write_text("b\n")
    t2 = _commit(work, "merged topic")
    _git(work, "merge", "--no-ff", "-m", "merge main", "main")
    mm = _git(work, "rev-parse", "HEAD")

    bare = tmp_path / "repos" / "octocat" / "hello.git"
    bare.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return {"c0": c0, "t1": t1, "m1": m1, "t2": t2, "mm": mm, "repos": str(tmp_path / "repos")}


@pytest.fixture
def backend(shas: dict[str, str]) -> GitBackend:
    return GitBackend(Path(shas["repos"]), GitErrorFactory(structlog.get_logger("test")))


def test_identical(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "main", shas["m1"])
    assert (result.status, result.ahead_by, result.behind_by) == ("identical", 0, 0)
    assert result.merge_base_sha == shas["m1"]
    assert result.commits == []
    assert result.files == []


def test_ahead(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "root", "feature/topic")
    assert (result.status, result.ahead_by, result.behind_by) == ("ahead", 1, 0)
    assert [(c.sha, c.parents) for c in result.commits] == [(shas["t1"], [shas["c0"]])]


def test_behind(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "main", "root")
    assert (result.status, result.ahead_by, result.behind_by) == ("behind", 0, 1)
    assert result.merge_base_sha == shas["c0"]
    assert result.commits == []


def test_diverged_diffs_merge_base_against_head(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "main", "feature/topic")
    assert (result.status, result.ahead_by, result.behind_by) == ("diverged", 1, 1)
    assert result.merge_base_sha == shas["c0"]
    assert [c.sha for c in result.commits] == [shas["t1"]]
    files = {f.filename: f for f in result.files}
    assert set(files) == {"new.txt", "moved.txt", "gone.txt"}
    assert files["new.txt"].status == "added"
    assert files["new.txt"].patch == "@@ -0,0 +1 @@\n+new"
    assert files["moved.txt"].status == "renamed"
    assert files["moved.txt"].previous_filename == "r.txt"
    assert files["gone.txt"].status == "removed"
    assert files["gone.txt"].previous_filename is None


def test_file_sha_is_the_blob_at_head(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "main", "feature/topic")
    new = next(f for f in result.files if f.filename == "new.txt")
    bare = Path(shas["repos"]) / "octocat" / "hello.git"
    assert new.sha == _git(bare, "rev-parse", f"{shas['t1']}:new.txt")


def test_merge_commit_range_includes_base_side_commits_with_parents(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "root", "feature/merged")
    assert (result.status, result.ahead_by, result.behind_by) == ("ahead", 3, 0)
    by_sha = {c.sha: c.parents for c in result.commits}
    assert set(by_sha) == {shas["t2"], shas["m1"], shas["mm"]}
    assert by_sha[shas["mm"]] == [shas["t2"], shas["m1"]]
    assert by_sha[shas["m1"]] == [shas["c0"]]
    assert result.commits[-1].sha == shas["mm"]


def test_merge_commit_against_current_base(backend: GitBackend, shas: dict[str, str]) -> None:
    result = backend.compare(REPO, "main", "feature/merged")
    assert (result.status, result.ahead_by, result.behind_by) == ("ahead", 2, 0)
    assert [c.sha for c in result.commits] == [shas["t2"], shas["mm"]]
    assert [f.filename for f in result.files] == ["b.txt"]


def test_unresolvable_ref_raises(backend: GitBackend) -> None:
    with pytest.raises(BranchNotFound):
        backend.compare(REPO, "main", "ghost")
