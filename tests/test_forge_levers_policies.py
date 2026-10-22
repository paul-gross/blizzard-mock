"""Named per-ref checks and the refusal levers (``blizzard-mock:unit-test``)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.forge_world import REPO, forge, open_pull, sha_of


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    with forge(tmp_path) as c:
        yield c


def _runs(client: TestClient, ref: str) -> dict[str, object]:
    return client.get(f"/repos/{REPO}/commits/{ref}/check-runs").json()


def _arm(client: TestClient, ref: str, *checks: tuple[str, str]) -> None:
    body = {"repo": REPO, "ref": ref, "checks": [{"name": n, "status": s} for n, s in checks]}
    assert client.post("/_levers/ref_checks", json=body).status_code == 200


def test_named_runs_are_armed_added_replaced_and_emptied(client: TestClient) -> None:
    _arm(client, "feature", ("build", "pending"))
    body = _runs(client, "feature")
    assert body["total_count"] == 1
    run = body["check_runs"][0]  # type: ignore[index]
    assert (run["name"], run["status"], run["conclusion"]) == ("build", "in_progress", None)

    _arm(client, "feature", ("lint", "success"))
    names = {r["name"]: r for r in _runs(client, "feature")["check_runs"]}  # type: ignore[attr-defined]
    assert set(names) == {"build", "lint"}
    assert names["lint"]["conclusion"] == "success"

    _arm(client, "feature", ("build", "failed"))
    names = {r["name"]: r for r in _runs(client, "feature")["check_runs"]}  # type: ignore[attr-defined]
    assert set(names) == {"build", "lint"}
    assert (names["build"]["status"], names["build"]["conclusion"]) == ("completed", "failure")

    _arm(client, "feature")
    assert _runs(client, "feature")["total_count"] == 0


def test_runs_attach_to_the_revision_not_the_spelling(client: TestClient) -> None:
    _arm(client, "feature", ("build", "failed"))
    by_sha = _runs(client, sha_of(client, "feature"))
    assert by_sha["total_count"] == 1
    # A revision nothing was armed against keeps the derived single run.
    assert _runs(client, "ahead")["check_runs"][0]["name"] == "ci"  # type: ignore[index]


def test_invalid_status_is_422(client: TestClient) -> None:
    body = {"repo": REPO, "ref": "main", "checks": [{"name": "x", "status": "weird"}]}
    assert client.post("/_levers/ref_checks", json=body).status_code == 422


def test_base_checks_failed_reads_red_for_the_base_tip_by_sha(client: TestClient) -> None:
    client.post("/_levers/base_checks_failed", json={"repo": REPO})
    assert _runs(client, "main")["check_runs"][0]["conclusion"] == "failure"  # type: ignore[index]
    assert _runs(client, sha_of(client, "main"))["check_runs"][0]["conclusion"] == "failure"  # type: ignore[index]
    assert _runs(client, "ahead")["check_runs"][0]["conclusion"] == "success"  # type: ignore[index]


def test_reset_clears_armed_runs(client: TestClient) -> None:
    _arm(client, "feature", ("build", "failed"))
    client.post("/_levers/reset")
    assert _runs(client, "feature")["check_runs"][0]["name"] == "ci"  # type: ignore[index]


def test_protected_branch_refuses_ref_update(client: TestClient) -> None:
    client.post("/_levers/branch_protected", json={"repo": REPO, "branch": "main"})
    resp = client.patch(f"/repos/{REPO}/git/refs/heads/main", json={"sha": sha_of(client, "ahead")})
    assert resp.status_code == 422
    assert resp.json()["message"].startswith("Protected branch update failed")
    # Another branch of the same repo is admitted.
    other = client.patch(f"/repos/{REPO}/git/refs/heads/feature", json={"sha": sha_of(client, "ahead"), "force": True})
    assert other.status_code == 200
    client.delete(f"/_levers/branch_protected?repo={REPO}&branch=main")
    assert client.patch(f"/repos/{REPO}/git/refs/heads/main", json={"sha": sha_of(client, "ahead")}).status_code == 200


def test_several_protected_branches_coexist(client: TestClient) -> None:
    client.post("/_levers/branch_protected", json={"repo": REPO, "branch": "main"})
    client.post("/_levers/branch_protected", json={"repo": REPO, "branch": "feature"})
    active = [lv for lv in client.get("/_levers").json()["active"] if lv["kind"] == "branch_protected"]
    assert {lv["branch"] for lv in active} == {"main", "feature"}


def test_ruleset_refuses_ref_create_and_branch_merge_on_matching_branches(client: TestClient) -> None:
    client.post("/_levers/ruleset", json={"repo": REPO, "pattern": "release/*"})
    main = sha_of(client, "main")
    refused = client.post(f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/release/1", "sha": main})
    assert refused.status_code == 422
    assert refused.json()["message"].startswith("Repository rule violations found")
    assert client.post(f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/topic", "sha": main}).status_code == 201

    client.post(f"/repos/{REPO}/git/refs", json={"ref": "refs/heads/release", "sha": main})
    client.post("/_levers/ruleset", json={"repo": REPO, "pattern": "ahead*"})
    merge = client.post(f"/repos/{REPO}/merges", json={"base": "ahead2", "head": "main"})
    assert merge.status_code == 422
    assert merge.json()["message"].startswith("Repository rule violations found")
    assert client.post(f"/repos/{REPO}/merges", json={"base": "feature", "head": "ahead"}).status_code in (201, 409)


def test_merge_commits_disallowed_refuses_only_the_merge_method(client: TestClient) -> None:
    client.post("/_levers/merge_commits_disallowed", json={"repo": REPO})
    first = open_pull(client, "ahead")["number"]
    refused = client.put(f"/repos/{REPO}/pulls/{first}/merge", json={"merge_method": "merge"})
    assert refused.status_code == 405
    assert refused.json()["message"] == "Merge commits are not allowed on this repository"
    assert client.put(f"/repos/{REPO}/pulls/{first}/merge", json={"merge_method": "squash"}).status_code == 200
    second = open_pull(client, "ahead2")["number"]
    assert client.put(f"/repos/{REPO}/pulls/{second}/merge", json={"merge_method": "rebase"}).status_code == 200


def test_scoped_levers_require_their_scope(client: TestClient) -> None:
    assert client.post("/_levers/branch_protected", json={"repo": REPO}).status_code == 422
    assert client.post("/_levers/ruleset", json={"repo": REPO}).status_code == 422
    assert client.post("/_levers/merge_commits_disallowed", json={}).status_code == 422
