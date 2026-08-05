"""In-memory forge-state store (``IWriteForgeState``).

Issue/PR metadata lives beside the bare repos as process-local state; git is
the durable truth for refs and commits, this holds the vendor metadata
(bodies, comments, merge dispositions) — in-memory is the right lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard_mock.forge.domain.models import Comment, Issue, Label, PullRequest


@dataclass
class _RepoState:
    issues: dict[int, Issue] = field(default_factory=dict)
    pulls: dict[int, PullRequest] = field(default_factory=dict)
    labels: dict[str, Label] = field(default_factory=dict)
    next_number: int = 1
    next_comment_id: int = 1


class InMemoryForgeState:
    """Process-local metadata store keyed by repo full name."""

    def __init__(self) -> None:
        self._repos: dict[str, _RepoState] = {}

    def _repo(self, repo: str) -> _RepoState:
        return self._repos.setdefault(repo, _RepoState())

    # -- reads -------------------------------------------------------------

    def get_issue(self, repo: str, number: int) -> Issue | None:
        return self._repo(repo).issues.get(number)

    def list_issues(self, repo: str, state: str | None, labels: list[str] | None = None) -> list[Issue]:
        issues = sorted(self._repo(repo).issues.values(), key=lambda i: i.number)
        if state not in (None, "all"):
            issues = [i for i in issues if i.state.value == state]
        if labels:
            issues = [i for i in issues if set(labels) <= set(i.labels)]
        return issues

    def get_pull(self, repo: str, number: int) -> PullRequest | None:
        return self._repo(repo).pulls.get(number)

    def list_pulls(self, repo: str, state: str | None) -> list[PullRequest]:
        pulls = sorted(self._repo(repo).pulls.values(), key=lambda p: p.number)
        if state in (None, "all"):
            return pulls
        return [p for p in pulls if p.state.value == state]

    def get_label(self, repo: str, name: str) -> Label | None:
        return self._repo(repo).labels.get(name)

    def list_labels(self, repo: str) -> list[Label]:
        return sorted(self._repo(repo).labels.values(), key=lambda label: label.name)

    # -- writes ------------------------------------------------------------

    def next_number(self, repo: str) -> int:
        state = self._repo(repo)
        number = state.next_number
        state.next_number += 1
        return number

    def next_comment_id(self, repo: str) -> int:
        state = self._repo(repo)
        cid = state.next_comment_id
        state.next_comment_id += 1
        return cid

    def put_issue(self, repo: str, issue: Issue) -> None:
        self._repo(repo).issues[issue.number] = issue

    def put_pull(self, repo: str, pull: PullRequest) -> None:
        self._repo(repo).pulls[pull.number] = pull

    def add_issue_comment(self, repo: str, number: int, comment: Comment) -> None:
        issue = self._repo(repo).issues[number]
        issue.comments.append(comment)

    def add_pull_comment(self, repo: str, number: int, comment: Comment) -> None:
        pull = self._repo(repo).pulls[number]
        pull.comments.append(comment)

    def put_label(self, repo: str, label: Label) -> None:
        self._repo(repo).labels[label.name] = label
