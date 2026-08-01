"""Unit + component coverage for the mock GitHub forge (``blizzard-mock:unit-test``).

Exercises the two seams over real bare git repos in a tmp dir: the work-source
seam (issues + comment threads, D-047 / D-074) and the delivery seam (PRs,
mergeability against real refs, real merges, D-057 / D-065), plus every lever.

The core delivery assertion is that ``create-issue → open-PR → merge → commit
reachable from bare main`` runs for real — the merged commit is an ancestor of
the base branch in the backing bare repo.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from blizzard_mock.forge import cli
from blizzard_mock.forge.app import create_app
from blizzard_mock.forge.config import ForgeConfig
from blizzard_mock.forge.domain.clock import FixedClock

REPO = "octocat/hello"
BARE_REL = Path("octocat/hello.git")


def _git(*args: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _build_bare_repos(root: Path) -> Path:
    """Mint a bare repo with branches: ``main`` (advanced), ``feature`` /
    ``feature2`` (clean merges into main), ``clash`` (real conflict),
    ``old-main`` (a snapshot of ``main`` before it advanced — a true ancestor
    of current ``main``, for fast-forward ref-update tests)."""
    work = root / "work"
    work.mkdir(parents=True)
    _git("init", "-b", "main", str(work))
    _git("-C", str(work), "config", "user.email", "seed@t")
    _git("-C", str(work), "config", "user.name", "seed")
    (work / "a.txt").write_text("hello\n")
    _git("-C", str(work), "add", "-A")
    _git("-C", str(work), "commit", "-m", "initial")
    _git("-C", str(work), "branch", "old-main")
    _git("-C", str(work), "checkout", "-b", "feature")
    (work / "feature.txt").write_text("feature\n")
    _git("-C", str(work), "add", "-A")
    _git("-C", str(work), "commit", "-m", "feat")
    _git("-C", str(work), "checkout", "-b", "feature2", "main")
    (work / "feature2.txt").write_text("feature2\n")
    _git("-C", str(work), "add", "-A")
    _git("-C", str(work), "commit", "-m", "feat2")
    _git("-C", str(work), "checkout", "-b", "clash", "main")
    (work / "a.txt").write_text("clash\n")
    _git("-C", str(work), "commit", "-am", "clash")
    _git("-C", str(work), "checkout", "main")
    (work / "a.txt").write_text("main change\n")
    _git("-C", str(work), "commit", "-am", "main change")
    bare = root / BARE_REL
    bare.parent.mkdir(parents=True)
    _git("clone", "--bare", str(work), str(bare))
    return root


def _is_ancestor(root: Path, ancestor: str, branch: str) -> bool:
    result = subprocess.run(["git", "--git-dir", str(root / BARE_REL), "merge-base", "--is-ancestor", ancestor, branch])
    return result.returncode == 0


@pytest.fixture
def repos_dir(tmp_path: Path) -> Path:
    return _build_bare_repos(tmp_path / "repos")


@pytest.fixture
def client(repos_dir: Path) -> Iterator[TestClient]:
    config = ForgeConfig(repos_dir=repos_dir, host="127.0.0.1", port=4421)
    clock = FixedClock(datetime(2026, 7, 13, tzinfo=UTC))
    with TestClient(create_app(config, clock=clock)) as test_client:
        yield test_client


def _open_pull(client: TestClient, head: str, base: str = "main") -> dict[str, Any]:
    return client.post(f"/repos/{REPO}/pulls", json={"title": head, "head": head, "base": base}).json()


# -- repositories ----------------------------------------------------------


def test_get_repo_reads_default_branch(client: TestClient) -> None:
    body = client.get(f"/repos/{REPO}").json()
    assert body["full_name"] == REPO
    assert body["default_branch"] == "main"
    assert body["owner"]["login"] == "octocat"


def test_missing_repo_is_404(client: TestClient) -> None:
    assert client.get("/repos/octocat/nope").status_code == 404


# -- work-source seam: issues + comment threads (D-047 / D-074) ------------


def test_issue_create_get_list(client: TestClient) -> None:
    created = client.post(f"/repos/{REPO}/issues", json={"title": "do work", "body": "spec"})
    assert created.status_code == 201
    number = created.json()["number"]

    got = client.get(f"/repos/{REPO}/issues/{number}").json()
    assert got["title"] == "do work"
    assert got["body"] == "spec"
    assert got["state"] == "open"

    listed = client.get(f"/repos/{REPO}/issues").json()
    assert [i["number"] for i in listed] == [number]


def test_issue_comment_thread(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    assert client.post(f"/repos/{REPO}/issues/{number}/comments", json={"body": "clarify"}).status_code == 201
    client.post(f"/repos/{REPO}/issues/{number}/comments", json={"body": "more"})

    comments = client.get(f"/repos/{REPO}/issues/{number}/comments").json()
    assert [c["body"] for c in comments] == ["clarify", "more"]
    assert client.get(f"/repos/{REPO}/issues/{number}").json()["comments"] == 2


def test_missing_issue_is_404(client: TestClient) -> None:
    assert client.get(f"/repos/{REPO}/issues/999").status_code == 404


def test_blank_title_rejected(client: TestClient) -> None:
    assert client.post(f"/repos/{REPO}/issues", json={"title": "  "}).status_code == 422


def test_close_issue_sets_state_and_reason(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]

    closed = client.patch(f"/repos/{REPO}/issues/{number}", json={"state": "closed", "state_reason": "completed"})
    assert closed.status_code == 200
    assert closed.json()["state"] == "closed"
    assert closed.json()["state_reason"] == "completed"

    got = client.get(f"/repos/{REPO}/issues/{number}").json()
    assert got["state"] == "closed"
    assert got["state_reason"] == "completed"


def test_reclose_issue_is_a_no_op(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    client.patch(f"/repos/{REPO}/issues/{number}", json={"state": "closed", "state_reason": "completed"})

    reclosed = client.patch(f"/repos/{REPO}/issues/{number}", json={"state": "closed", "state_reason": "completed"})
    assert reclosed.status_code == 200
    assert reclosed.json()["state"] == "closed"
    assert reclosed.json()["state_reason"] == "completed"


def test_close_unknown_issue_is_404(client: TestClient) -> None:
    assert client.patch(f"/repos/{REPO}/issues/999", json={"state": "closed"}).status_code == 404


def test_close_issue_invalid_state_rejected(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    assert client.patch(f"/repos/{REPO}/issues/{number}", json={"state": "sideways"}).status_code == 422


def test_closed_issue_moves_between_state_filters(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    assert number in [i["number"] for i in client.get(f"/repos/{REPO}/issues", params={"state": "open"}).json()]

    client.patch(f"/repos/{REPO}/issues/{number}", json={"state": "closed", "state_reason": "completed"})

    open_numbers = [i["number"] for i in client.get(f"/repos/{REPO}/issues", params={"state": "open"}).json()]
    closed_numbers = [i["number"] for i in client.get(f"/repos/{REPO}/issues", params={"state": "closed"}).json()]
    assert number not in open_numbers
    assert number in closed_numbers


# -- labels: repo-level definitions + issue assignment ----------------------


def test_create_and_list_repo_labels(client: TestClient) -> None:
    created = client.post(f"/repos/{REPO}/labels", json={"name": "blizzard:ingested"})
    assert created.status_code == 201
    assert created.json() == {"name": "blizzard:ingested"}

    listed = client.get(f"/repos/{REPO}/labels").json()
    assert listed == [{"name": "blizzard:ingested"}]


def test_create_duplicate_repo_label_is_422(client: TestClient) -> None:
    client.post(f"/repos/{REPO}/labels", json={"name": "blizzard:ingested"})
    dup = client.post(f"/repos/{REPO}/labels", json={"name": "blizzard:ingested"})
    assert dup.status_code == 422


def test_add_and_list_issue_labels(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]

    added = client.post(f"/repos/{REPO}/issues/{number}/labels", json=["blizzard:ingested"])
    assert added.status_code == 200
    assert added.json() == [{"name": "blizzard:ingested"}]

    listed = client.get(f"/repos/{REPO}/issues/{number}/labels").json()
    assert listed == [{"name": "blizzard:ingested"}]

    got = client.get(f"/repos/{REPO}/issues/{number}").json()
    assert got["labels"] == [{"name": "blizzard:ingested"}]


def test_add_issue_label_is_idempotent(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    client.post(f"/repos/{REPO}/issues/{number}/labels", json=["blizzard:ingested"])
    again = client.post(f"/repos/{REPO}/issues/{number}/labels", json=["blizzard:ingested"])
    assert again.json() == [{"name": "blizzard:ingested"}]


def test_remove_issue_label(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    client.post(f"/repos/{REPO}/issues/{number}/labels", json=["blizzard:ingested", "blizzard:in-progress"])

    removed = client.delete(f"/repos/{REPO}/issues/{number}/labels/blizzard:ingested")
    assert removed.status_code == 200
    assert removed.json() == [{"name": "blizzard:in-progress"}]


def test_remove_absent_issue_label_is_404(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    resp = client.delete(f"/repos/{REPO}/issues/{number}/labels/blizzard:ingested")
    assert resp.status_code == 404


def test_list_issues_filters_by_labels_and_composes_with_state_all(client: TestClient) -> None:
    a = client.post(f"/repos/{REPO}/issues", json={"title": "a"}).json()["number"]
    b = client.post(f"/repos/{REPO}/issues", json={"title": "b"}).json()["number"]
    client.post(f"/repos/{REPO}/issues/{a}/labels", json=["blizzard:ingested", "blizzard:in-progress"])
    client.post(f"/repos/{REPO}/issues/{b}/labels", json=["blizzard:ingested"])

    only_ingested = client.get(f"/repos/{REPO}/issues", params={"labels": "blizzard:ingested", "state": "all"}).json()
    assert {i["number"] for i in only_ingested} == {a, b}

    both = client.get(
        f"/repos/{REPO}/issues", params={"labels": "blizzard:ingested,blizzard:in-progress", "state": "all"}
    ).json()
    assert {i["number"] for i in both} == {a}


def test_list_issues_labels_filter_still_honors_state(client: TestClient) -> None:
    number = client.post(f"/repos/{REPO}/issues", json={"title": "closes"}).json()["number"]
    client.post(f"/repos/{REPO}/issues/{number}/labels", json=["blizzard:ingested"])
    # default state=open still applies alongside the labels filter
    open_only = client.get(f"/repos/{REPO}/issues", params={"labels": "blizzard:ingested"}).json()
    assert {i["number"] for i in open_only} == {number}


# -- delivery seam: pull requests (D-057 / D-065) --------------------------


def test_pull_mergeability_computed_against_real_refs(client: TestClient) -> None:
    clean = _open_pull(client, "feature")
    assert clean["mergeable"] is True
    assert clean["mergeable_state"] == "clean"
    assert clean["head"]["ref"] == "feature"
    assert len(clean["head"]["sha"]) == 40

    conflict = _open_pull(client, "clash")
    assert conflict["mergeable"] is False
    assert conflict["mergeable_state"] == "dirty"


def test_create_pull_unknown_branch_rejected(client: TestClient) -> None:
    resp = client.post(f"/repos/{REPO}/pulls", json={"title": "x", "head": "ghost", "base": "main"})
    assert resp.status_code == 422


def test_duplicate_open_pull_is_422(client: TestClient) -> None:
    first = _open_pull(client, "feature")
    before = client.get(f"/repos/{REPO}/pulls").json()

    dup = client.post(f"/repos/{REPO}/pulls", json={"title": "x", "head": "feature", "base": "main"})
    assert dup.status_code == 422
    assert dup.json()["message"] == "A pull request already exists for octocat:feature."

    after = client.get(f"/repos/{REPO}/pulls").json()
    assert len(after) == len(before)
    assert first["number"] in [p["number"] for p in after]


def test_duplicate_pull_allowed_once_prior_is_closed(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    closed = client.patch(f"/repos/{REPO}/pulls/{number}", json={"state": "closed"}).json()
    assert closed["state"] == "closed"

    reopened = client.post(f"/repos/{REPO}/pulls", json={"title": "x", "head": "feature", "base": "main"})
    assert reopened.status_code == 201


def test_duplicate_pull_allowed_once_prior_is_merged(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    assert client.put(f"/repos/{REPO}/pulls/{number}/merge", json={}).status_code == 200

    reopened = client.post(f"/repos/{REPO}/pulls", json={"title": "x", "head": "feature", "base": "main"})
    assert reopened.status_code == 201


def test_same_head_different_base_not_rejected(client: TestClient) -> None:
    _open_pull(client, "feature", base="main")
    other_base = client.post(f"/repos/{REPO}/pulls", json={"title": "x", "head": "feature", "base": "old-main"})
    assert other_base.status_code == 201


def test_merge_lands_commit_reachable_from_bare_main(client: TestClient, repos_dir: Path) -> None:
    pull = _open_pull(client, "feature")
    head_sha = pull["head"]["sha"]
    number = pull["number"]

    merged = client.put(f"/repos/{REPO}/pulls/{number}/merge", json={})
    assert merged.status_code == 200
    merge_sha = merged.json()["sha"]
    assert merged.json()["merged"] is True

    after = client.get(f"/repos/{REPO}/pulls/{number}").json()
    assert after["merged"] is True
    assert after["merge_commit_sha"] == merge_sha
    assert after["state"] == "closed"
    assert client.get(f"/repos/{REPO}/pulls/{number}/merge").status_code == 204

    # The real assertion: the head commit is now reachable from bare main.
    assert _is_ancestor(repos_dir, head_sha, "refs/heads/main")
    assert _is_ancestor(repos_dir, merge_sha, "refs/heads/main")


def test_real_conflict_merge_is_405(client: TestClient) -> None:
    number = _open_pull(client, "clash")["number"]
    assert client.put(f"/repos/{REPO}/pulls/{number}/merge", json={}).status_code == 405


def test_merge_stale_sha_is_409(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    resp = client.put(f"/repos/{REPO}/pulls/{number}/merge", json={"sha": "0" * 40})
    assert resp.status_code == 409


def test_double_merge_is_405(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    assert client.put(f"/repos/{REPO}/pulls/{number}/merge", json={}).status_code == 200
    assert client.put(f"/repos/{REPO}/pulls/{number}/merge", json={}).status_code == 405


def test_close_without_merge_is_terminal(client: TestClient) -> None:
    """Close-without-merge is a terminal disposition a delivery flow treats as
    complete (D-065)."""
    number = _open_pull(client, "feature")["number"]
    closed = client.patch(f"/repos/{REPO}/pulls/{number}", json={"state": "closed"}).json()
    assert closed["state"] == "closed"
    assert closed["merged"] is False
    assert client.get(f"/repos/{REPO}/pulls/{number}/merge").status_code == 404


# -- git data: refs + commits ----------------------------------------------


def test_get_ref_and_commit(client: TestClient) -> None:
    ref = client.get(f"/repos/{REPO}/git/ref/heads/main").json()
    assert ref["ref"] == "refs/heads/main"
    sha = ref["object"]["sha"]

    commit = client.get(f"/repos/{REPO}/commits/{sha}").json()
    assert commit["sha"] == sha
    assert commit["commit"]["message"].strip() == "main change"


# -- git data: ref write (PR-free, fast-forward delivery) ------------------


def test_update_ref_fast_forward_moves_the_ref(client: TestClient) -> None:
    # ``old-main`` is a true ancestor of ``main``'s current tip.
    before = client.get(f"/repos/{REPO}/git/ref/heads/old-main").json()["object"]["sha"]
    target = client.get(f"/repos/{REPO}/git/ref/heads/main").json()["object"]["sha"]
    assert target != before

    resp = client.patch(f"/repos/{REPO}/git/refs/heads/old-main", json={"sha": target})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ref"] == "refs/heads/old-main"
    assert body["object"]["sha"] == target

    after = client.get(f"/repos/{REPO}/git/ref/heads/old-main").json()["object"]["sha"]
    assert after == target


def test_update_ref_non_fast_forward_rejected_and_ref_unchanged(client: TestClient) -> None:
    before = client.get(f"/repos/{REPO}/git/ref/heads/main").json()["object"]["sha"]
    other = client.get(f"/repos/{REPO}/git/ref/heads/feature2").json()["object"]["sha"]

    resp = client.patch(f"/repos/{REPO}/git/refs/heads/main", json={"sha": other})
    assert resp.status_code == 422

    after = client.get(f"/repos/{REPO}/git/ref/heads/main").json()["object"]["sha"]
    assert after == before


def test_update_ref_force_performs_non_fast_forward_update(client: TestClient) -> None:
    other = client.get(f"/repos/{REPO}/git/ref/heads/feature2").json()["object"]["sha"]

    resp = client.patch(f"/repos/{REPO}/git/refs/heads/main", json={"sha": other, "force": True})
    assert resp.status_code == 200
    assert resp.json()["object"]["sha"] == other

    after = client.get(f"/repos/{REPO}/git/ref/heads/main").json()["object"]["sha"]
    assert after == other


def test_update_ref_unknown_ref_is_422(client: TestClient) -> None:
    sha = client.get(f"/repos/{REPO}/git/ref/heads/main").json()["object"]["sha"]
    resp = client.patch(f"/repos/{REPO}/git/refs/heads/ghost", json={"sha": sha})
    assert resp.status_code == 422


def test_update_ref_unknown_sha_is_422(client: TestClient) -> None:
    resp = client.patch(f"/repos/{REPO}/git/refs/heads/feature2", json={"sha": "0" * 40})
    assert resp.status_code == 422


# -- levers: catalog -------------------------------------------------------


def test_lever_catalog_lists_every_kind(client: TestClient) -> None:
    body = client.get("/_levers").json()
    kinds = set(body["state_levers"]) | set(body["action_levers"])
    assert kinds == {
        "externally_merged",
        "merge_conflict",
        "merge_rejected",
        "comment_midflight",
        "rate_limited",
        "token_rejected",
        "unreachable",
        "stale_branch",
        "checks_pending",
    }


# -- levers: per-PR delivery states ----------------------------------------


def test_merge_conflict_lever(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    client.post("/_levers/merge_conflict", json={"repo": REPO, "number": number})

    view = client.get(f"/repos/{REPO}/pulls/{number}").json()
    assert view["mergeable"] is False
    assert view["mergeable_state"] == "dirty"
    assert client.put(f"/repos/{REPO}/pulls/{number}/merge", json={}).status_code == 405

    client.delete(f"/_levers/merge_conflict?repo={REPO}&number={number}")
    assert client.get(f"/repos/{REPO}/pulls/{number}").json()["mergeable"] is True


def test_merge_rejected_lever(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    client.post("/_levers/merge_rejected", json={"repo": REPO, "number": number, "message": "required checks"})
    resp = client.put(f"/repos/{REPO}/pulls/{number}/merge", json={})
    assert resp.status_code == 405
    assert "required checks" in resp.json()["message"]


def test_checks_pending_lever_reads_blocked(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    client.post("/_levers/checks_pending", json={"repo": REPO, "number": number})

    assert client.get(f"/repos/{REPO}/pulls/{number}").json()["mergeable_state"] == "blocked"

    # clearing the lever stands in for "CI went green" — the PR reads clean again
    client.delete(f"/_levers/checks_pending?repo={REPO}&number={number}")
    assert client.get(f"/repos/{REPO}/pulls/{number}").json()["mergeable_state"] == "clean"


def test_stale_branch_lever_reads_behind_and_update_branch_self_heals(client: TestClient) -> None:
    pull = _open_pull(client, "feature")
    number = pull["number"]
    original_head = pull["head"]["sha"]
    client.post("/_levers/stale_branch", json={"repo": REPO, "number": number})

    behind = client.get(f"/repos/{REPO}/pulls/{number}").json()
    assert behind["mergeable_state"] == "behind"

    # update-branch: 202, advances the head (new sha), clears the behind state → clean
    resp = client.put(f"/repos/{REPO}/pulls/{number}/update-branch", json={"expected_head_sha": original_head})
    assert resp.status_code == 202
    healed = client.get(f"/repos/{REPO}/pulls/{number}").json()
    assert healed["mergeable_state"] == "clean"
    assert healed["head"]["sha"] != original_head, "update-branch must advance the head sha"

    # the moved head merges cleanly by its new sha
    assert client.put(f"/repos/{REPO}/pulls/{number}/merge", json={"sha": healed["head"]["sha"]}).status_code == 200


def test_update_branch_stale_expected_head_is_409(client: TestClient) -> None:
    number = _open_pull(client, "feature")["number"]
    client.post("/_levers/stale_branch", json={"repo": REPO, "number": number})
    resp = client.put(f"/repos/{REPO}/pulls/{number}/update-branch", json={"expected_head_sha": "deadbeef"})
    assert resp.status_code == 409


def test_externally_merged_lever_lands_and_is_detectable(client: TestClient, repos_dir: Path) -> None:
    """The external-merge lever performs the direct push to main a polling
    delivery flow must then detect (D-065)."""
    pull = _open_pull(client, "feature2")
    number = pull["number"]
    head_sha = pull["head"]["sha"]

    fired = client.post("/_levers/externally_merged", json={"repo": REPO, "number": number})
    assert fired.status_code == 200

    view = client.get(f"/repos/{REPO}/pulls/{number}").json()
    assert view["merged"] is True
    assert view["merged_by"]["login"] == "external"
    assert client.get(f"/repos/{REPO}/pulls/{number}/merge").status_code == 204
    assert _is_ancestor(repos_dir, head_sha, "refs/heads/main")


def test_comment_midflight_lever(client: TestClient) -> None:
    """A comment appears mid-flight on a live thread (D-074)."""
    number = client.post(f"/repos/{REPO}/issues", json={"title": "t"}).json()["number"]
    client.post("/_levers/comment_midflight", json={"repo": REPO, "number": number, "body": "reviewer note"})
    comments = client.get(f"/repos/{REPO}/issues/{number}/comments").json()
    assert [c["body"] for c in comments] == ["reviewer note"]


# -- levers: request-scoped edge states ------------------------------------


def test_token_rejected_lever(client: TestClient) -> None:
    client.post("/_levers/token_rejected", json={})
    resp = client.get(f"/repos/{REPO}")
    assert resp.status_code == 401
    assert resp.json()["message"] == "Bad credentials"
    client.delete("/_levers/token_rejected")
    assert client.get(f"/repos/{REPO}").status_code == 200


def test_unreachable_lever(client: TestClient) -> None:
    client.post("/_levers/unreachable", json={})
    assert client.get(f"/repos/{REPO}").status_code == 503
    client.delete("/_levers/unreachable")
    assert client.get(f"/repos/{REPO}").status_code == 200


def test_rate_limited_lever_self_expires(client: TestClient) -> None:
    client.post("/_levers/rate_limited", json={"remaining": 1})
    limited = client.get(f"/repos/{REPO}")
    assert limited.status_code == 403
    assert limited.headers["X-RateLimit-Remaining"] == "0"
    # remaining was 1; the affected request consumed it, so the next passes.
    assert client.get(f"/repos/{REPO}").status_code == 200


def test_rate_limited_lever_scoped_to_repo(client: TestClient) -> None:
    client.post("/_levers/rate_limited", json={"repo": REPO})
    assert client.get(f"/repos/{REPO}").status_code == 403
    # A different repo is unaffected (resolves normally — here a 404).
    assert client.get("/repos/octocat/other").status_code == 404


def test_reset_clears_all_levers(client: TestClient) -> None:
    client.post("/_levers/token_rejected", json={})
    client.post("/_levers/reset")
    assert client.get(f"/repos/{REPO}").status_code == 200
    assert client.get("/_levers").json()["active"] == []


def test_token_honored_when_no_lever(client: TestClient) -> None:
    """Any token is accepted unless the reject lever is pulled."""
    resp = client.get(f"/repos/{REPO}", headers={"Authorization": "token whatever"})
    assert resp.status_code == 200


# -- entrypoint ------------------------------------------------------------


def test_forge_entrypoint_is_wired() -> None:
    assert hasattr(cli, "main")
    assert callable(cli.main)
