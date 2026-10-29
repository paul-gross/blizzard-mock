from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / ".github/actions/resolve-blizzard-ref/resolve-blizzard-ref.sh"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
}


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}
    ).stdout.strip()


@pytest.fixture(scope="module")
def blizzard(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    root = tmp_path_factory.mktemp("blizzard")
    work = root / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "master")
    shas: dict[str, str] = {}
    for name in ("master", "release/0.2", "feat/x"):
        _git(work, "checkout", "-q", "-B", name, *(["master"] if name != "master" else []))
        _git(work, "commit", "-q", "--allow-empty", "-m", name)
        shas[name] = _git(work, "rev-parse", "HEAD")
    _git(work, "tag", "-a", "v0.2.0", shas["release/0.2"], "-m", "v0.2.0")
    shas["tag-object"] = _git(work, "rev-parse", "v0.2.0")
    bare = root / "blizzard.git"
    _git(root, "clone", "-q", "--bare", str(work), str(bare))
    shas["url"] = bare.as_uri()
    return shas


def _resolve(blizzard: dict[str, str], **context: str) -> subprocess.CompletedProcess[str]:
    keys = {"EXPLICIT_REF", "EVENT_NAME", "REF", "BASE_REF", "HEAD_REF"}
    env = {k: v for k, v in os.environ.items() if k not in keys}
    env.update(BLIZZARD_URL=blizzard["url"], **context)
    return subprocess.run(["bash", str(_SCRIPT)], capture_output=True, text=True, env=env)


def _outputs(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_tag_peels_to_commit(blizzard: dict[str, str]) -> None:
    out = _outputs(_resolve(blizzard, EVENT_NAME="push", REF="refs/tags/v0.2.0"))
    assert out == {"ref": "v0.2.0", "sha": blizzard["release/0.2"]}
    assert out["sha"] != blizzard["tag-object"]


@pytest.mark.parametrize(
    ("base", "head", "selected"),
    [
        ("master", "feat/x", "feat/x"),
        ("master", "fix/absent", "master"),
        ("release/0.2", "feat/x", "feat/x"),
        ("release/0.2", "fix/absent", "release/0.2"),
    ],
)
def test_pull_request_branch_selection(blizzard: dict[str, str], base: str, head: str, selected: str) -> None:
    assert _outputs(_resolve(blizzard, EVENT_NAME="pull_request", BASE_REF=base, HEAD_REF=head)) == {
        "ref": selected,
        "sha": blizzard[selected],
    }


def test_master_push_ignores_head(blizzard: dict[str, str]) -> None:
    assert _outputs(_resolve(blizzard, EVENT_NAME="push", REF="refs/heads/master", HEAD_REF="feat/x")) == {
        "ref": "master",
        "sha": blizzard["master"],
    }


@pytest.mark.parametrize("explicit", ["master", "feat/x", "v0.2.0"])
def test_explicit_ref_overrides_event(blizzard: dict[str, str], explicit: str) -> None:
    selected = "release/0.2" if explicit == "v0.2.0" else explicit
    assert _outputs(_resolve(blizzard, EXPLICIT_REF=explicit, EVENT_NAME="workflow_dispatch")) == {
        "ref": explicit,
        "sha": blizzard[selected],
    }


@pytest.mark.parametrize(
    ("context", "missing"),
    [
        ({"EVENT_NAME": "push", "REF": "refs/tags/v9.9.9"}, "v9.9.9"),
        ({"EVENT_NAME": "pull_request", "BASE_REF": "release/9.9", "HEAD_REF": "absent"}, "release/9.9"),
        ({"EXPLICIT_REF": "absent", "EVENT_NAME": "schedule"}, "absent"),
        ({"EVENT_NAME": "push", "REF": "refs/heads/release/0.2"}, "matches no"),
        ({"EVENT_NAME": "pull_request", "BASE_REF": "develop"}, "matches no"),
        ({"EVENT_NAME": "schedule", "REF": "refs/heads/master"}, "matches no"),
    ],
)
def test_missing_refs_and_unmatched_events_fail_closed(
    blizzard: dict[str, str], context: dict[str, str], missing: str
) -> None:
    result = _resolve(blizzard, **context)
    assert result.returncode != 0
    assert missing in result.stderr
    assert result.stdout == ""
