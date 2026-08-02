"""Render forge domain objects into vendor-native GitHub REST v3 JSON.

The wire shape lives here, not in the domain (``bzh:domain-core``): URLs,
nested ``user``/``owner`` login objects, and ``mergeable_state`` all get built
against the configured ``base_url`` so a GitHub-shaped client consumes the mock
unmodified.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from blizzard_mock.forge.domain.git import GitCommit
from blizzard_mock.forge.domain.levers import Lever
from blizzard_mock.forge.domain.models import CheckRun, Comment, Issue, Label, Repo
from blizzard_mock.forge.domain.service import MergeResult, PullView


def _ts(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _user(login: str) -> dict[str, Any]:
    return {"login": login, "type": "User"}


def error_json(message: str) -> dict[str, Any]:
    return {"message": message, "documentation_url": "https://docs.github.com/rest"}


def repo_json(repo: Repo, base_url: str) -> dict[str, Any]:
    full = repo.full_name
    return {
        "id": abs(hash(full)) % (10**8),
        "name": repo.name,
        "full_name": full,
        "owner": _user(repo.owner),
        "private": False,
        "default_branch": repo.default_branch,
        "html_url": f"{base_url}/{full}",
        "url": f"{base_url}/repos/{full}",
    }


def issue_json(repo_full: str, issue: Issue, base_url: str, *, is_pull: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "number": issue.number,
        "title": issue.title,
        "body": issue.body,
        "state": issue.state.value,
        "state_reason": issue.state_reason,
        "user": _user(issue.user),
        "labels": issue_labels_json(issue.labels),
        "comments": len(issue.comments),
        "created_at": _ts(issue.created_at),
        "updated_at": _ts(issue.updated_at),
        "html_url": f"{base_url}/{repo_full}/issues/{issue.number}",
        "url": f"{base_url}/repos/{repo_full}/issues/{issue.number}",
        "comments_url": f"{base_url}/repos/{repo_full}/issues/{issue.number}/comments",
    }
    if is_pull:
        payload["pull_request"] = {
            "url": f"{base_url}/repos/{repo_full}/pulls/{issue.number}",
            "html_url": f"{base_url}/{repo_full}/pull/{issue.number}",
        }
    return payload


def issue_labels_json(names: list[str]) -> list[dict[str, Any]]:
    return [{"name": name} for name in names]


def label_json(label: Label) -> dict[str, Any]:
    return {"name": label.name}


def comment_json(repo_full: str, number: int, comment: Comment, base_url: str) -> dict[str, Any]:
    return {
        "id": comment.id,
        "body": comment.body,
        "user": _user(comment.user),
        "created_at": _ts(comment.created_at),
        "updated_at": _ts(comment.updated_at),
        "html_url": f"{base_url}/{repo_full}/issues/{number}#issuecomment-{comment.id}",
        "url": f"{base_url}/repos/{repo_full}/issues/comments/{comment.id}",
    }


def pull_json(repo_full: str, view: PullView, base_url: str) -> dict[str, Any]:
    pull = view.pull
    return {
        "number": pull.number,
        "title": pull.title,
        "body": pull.body,
        "state": pull.state.value,
        "user": _user(pull.user),
        "created_at": _ts(pull.created_at),
        "updated_at": _ts(pull.updated_at),
        "merged": pull.merged,
        "merged_at": _ts(pull.merged_at) if pull.merged_at else None,
        "merged_by": _user(pull.merged_by) if pull.merged_by else None,
        "merge_commit_sha": pull.merge_commit_sha,
        "mergeable": view.mergeable,
        "mergeable_state": view.mergeable_state.value,
        "comments": len(pull.comments),
        "head": {"ref": pull.head, "sha": view.head_sha, "label": pull.head},
        "base": {"ref": pull.base, "sha": view.base_sha, "label": pull.base},
        "html_url": f"{base_url}/{repo_full}/pull/{pull.number}",
        "url": f"{base_url}/repos/{repo_full}/pulls/{pull.number}",
    }


def merge_result_json(result: MergeResult) -> dict[str, Any]:
    return {"sha": result.sha, "merged": result.merged, "message": result.message}


def commit_json(repo_full: str, commit: GitCommit, base_url: str) -> dict[str, Any]:
    return {
        "sha": commit.sha,
        "url": f"{base_url}/repos/{repo_full}/commits/{commit.sha}",
        "html_url": f"{base_url}/{repo_full}/commit/{commit.sha}",
        "commit": {
            "message": commit.message,
            "author": {
                "name": commit.author.name,
                "email": commit.author.email,
                "date": commit.author.date,
            },
        },
        "parents": [{"sha": sha} for sha in commit.parents],
    }


def ref_json(repo_full: str, ref_name: str, sha: str, base_url: str) -> dict[str, Any]:
    return {
        "ref": ref_name,
        "url": f"{base_url}/repos/{repo_full}/git/{ref_name}",
        "object": {"sha": sha, "type": "commit"},
    }


def check_run_json(repo_full: str, run: CheckRun, base_url: str) -> dict[str, Any]:
    return {
        "id": run.id,
        "name": run.name,
        "status": run.status,
        "conclusion": run.conclusion,
        "details_url": f"{base_url}/{repo_full}/runs/{run.id}",
        "head_sha": run.head_sha,
    }


def lever_json(lever: Lever) -> dict[str, Any]:
    return {
        "kind": lever.kind.value,
        "repo": lever.repo,
        "number": lever.number,
        "remaining": lever.remaining,
        "message": lever.message,
    }
