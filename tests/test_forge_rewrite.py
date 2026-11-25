"""Git-backed repository method settings and rewrite evidence."""

import base64
import subprocess
from pathlib import Path

import pytest

from tests.forge_world import REPO, bare_git, forge, open_pull


@pytest.mark.parametrize("mask", range(8))
def test_settings_enforce_each_method(tmp_path: Path, mask: int) -> None:
    methods = [
        ("merge", "merge_commit", "merge_commits_disallowed"),
        ("rebase", "rebase_merge", "rebase_merge_disallowed"),
        ("squash", "squash_merge", "squash_merge_disallowed"),
    ]
    with forge(tmp_path) as client:
        for i, (_, _, lever) in enumerate(methods):
            if not mask & (1 << i):
                assert client.post(f"/_levers/{lever}", json={"repo": REPO}).status_code == 200
        settings = client.get(f"/repos/{REPO}").json()
        for i, (method, field, _) in enumerate(methods):
            assert settings[f"allow_{field}"] is bool(mask & (1 << i))
            pull = open_pull(client, "ahead")
            response = client.put(f"/repos/{REPO}/pulls/{pull['number']}/merge", json={"merge_method": method})
            assert response.status_code == (200 if mask & (1 << i) else 405)
            if response.status_code != 200:
                client.patch(f"/repos/{REPO}/pulls/{pull['number']}", json={"state": "closed"})


def test_rebase_update_and_immutable_objects(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        pull = open_pull(client, "feature")
        old = pull["head"]["sha"]
        base = pull["base"]["sha"]
        query = "mutation($input: UpdatePullRequestBranchInput!) { updatePullRequestBranch(input: $input) { pullRequest { id headRefOid } } }"
        args = {"pullRequestId": pull["node_id"], "expectedHeadOid": "0" * 40, "updateMethod": "REBASE"}
        failed = client.post("/graphql", json={"query": query, "variables": {"input": args}}).json()
        assert "expected head oid" in failed["errors"][0]["message"].lower()
        assert bare_git(tmp_path, "rev-parse", "feature") == old
        args["expectedHeadOid"] = old
        response = client.post("/graphql", json={"query": query, "variables": {"input": args}}).json()
        head = response["data"]["updatePullRequestBranch"]["pullRequest"]["headRefOid"]
        assert head != old
        assert bare_git(tmp_path, "rev-parse", "main") == base
        assert bare_git(tmp_path, "rev-parse", f"{head}^") == base
        commit = client.get(f"/repos/{REPO}/git/commits/{head}").json()
        assert commit["parents"] == [{"sha": base}]
        old_commit = client.get(f"/repos/{REPO}/git/commits/{old}").json()
        assert commit["author"] == old_commit["author"]
        assert commit["message"] == old_commit["message"]
        tree = client.get(f"/repos/{REPO}/git/trees/{commit['tree']['sha']}?recursive=1").json()
        assert tree["truncated"] is False
        entry = next(e for e in tree["tree"] if e["path"] == "feature.txt")
        blob = client.get(f"/repos/{REPO}/git/blobs/{entry['sha']}").json()
        assert base64.b64decode(blob["content"]) == b"feature\n"
        assert client.get(f"/repos/{REPO}/git/commits/{old}").json()["sha"] == old


def test_method_refusals_are_scoped_and_clearable(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        pull = open_pull(client, "ahead")
        for method in ("merge", "rebase"):
            assert (
                client.post("/_levers/merge_method_refused", json={"repo": REPO, "method": method}).status_code == 200
            )
        for method in ("merge", "rebase"):
            response = client.put(f"/repos/{REPO}/pulls/{pull['number']}/merge", json={"merge_method": method})
            assert response.status_code == 405
            assert (
                response.json()["message"]
                .lower()
                .startswith(
                    {"merge": "merge commits are not allowed", "rebase": "rebase merges are not allowed"}[method]
                )
            )
        assert client.get(f"/repos/{REPO}").json()["allow_merge_commit"] is True
        client.delete("/_levers/merge_method_refused", params={"repo": REPO})
        assert (
            client.put(f"/repos/{REPO}/pulls/{pull['number']}/merge", json={"merge_method": "merge"}).status_code == 200
        )


def test_rebase_conflict_leaves_refs_unchanged(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        pull = open_pull(client, "clash")
        query = "mutation($input: UpdatePullRequestBranchInput!) { updatePullRequestBranch(input: $input) { pullRequest { id } } }"
        response = client.post(
            "/graphql",
            json={
                "query": query,
                "variables": {
                    "input": {
                        "pullRequestId": pull["node_id"],
                        "expectedHeadOid": pull["head"]["sha"],
                        "updateMethod": "REBASE",
                    }
                },
            },
        ).json()
        assert response["errors"]
        assert bare_git(tmp_path, "rev-parse", "clash") == pull["head"]["sha"]
        assert bare_git(tmp_path, "rev-parse", "main") == pull["base"]["sha"]


def test_rebase_update_preserves_each_commit(tmp_path: Path) -> None:
    with forge(tmp_path) as client:
        work = tmp_path / "repos" / "work"

        def git(*args: str) -> None:
            subprocess.run(["git", "-C", str(work), *args], check=True, capture_output=True)

        git("checkout", "feature")
        (work / "second.txt").write_text("second\n")
        git("add", "second.txt")
        git("commit", "-m", "second")
        git("push", str(tmp_path / "repos" / "octocat" / "hello.git"), "feature")
        pull = open_pull(client, "feature")
        old = pull["head"]["sha"]
        response = client.post(
            "/graphql",
            json={
                "query": "mutation { updatePullRequestBranch }",
                "variables": {
                    "input": {"pullRequestId": pull["node_id"], "expectedHeadOid": old, "updateMethod": "REBASE"}
                },
            },
        ).json()
        head = response["data"]["updatePullRequestBranch"]["pullRequest"]["headRefOid"]
        assert bare_git(tmp_path, "rev-parse", f"{head}~2") == pull["base"]["sha"]
        for original, rewritten in ((old, head), (f"{old}^", f"{head}^")):
            before = client.get(f"/repos/{REPO}/git/commits/{bare_git(tmp_path, 'rev-parse', original)}").json()
            after = client.get(f"/repos/{REPO}/git/commits/{bare_git(tmp_path, 'rev-parse', rewritten)}").json()
            assert (before["author"], before["message"]) == (after["author"], after["message"])
        assert bare_git(tmp_path, "rev-parse", "main") == pull["base"]["sha"]
