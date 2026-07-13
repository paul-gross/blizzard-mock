"""The forge-state seam — issue and pull-request metadata.

Git is the on-disk truth for refs and commits; the *metadata* around them —
issue bodies, comment threads, PR merge dispositions — is forge state held
behind this seam (the in-memory adapter lives in ``forge.internal.state_store``).
Split read/write per ``bzh:repository-split``: the read routes hold the read-only
Protocol, the domain service holds the write one. Issue and pull numbers are
drawn from **one shared per-repo counter**, mirroring GitHub.
"""

from __future__ import annotations

from typing import Protocol

from blizzard_mock.forge.domain.models import Comment, Issue, PullRequest


class IReadForgeState(Protocol):
    """Read-only queries over issue/PR metadata. Read routes depend on this."""

    def get_issue(self, repo: str, number: int) -> Issue | None: ...
    def list_issues(self, repo: str, state: str | None) -> list[Issue]: ...
    def get_pull(self, repo: str, number: int) -> PullRequest | None: ...
    def list_pulls(self, repo: str, state: str | None) -> list[PullRequest]: ...


class IWriteForgeState(IReadForgeState, Protocol):
    """Read-write variant. Only the domain (``ForgeService``) depends on this."""

    def next_number(self, repo: str) -> int:
        """Draw the next issue/PR number from the shared per-repo counter."""
        ...

    def put_issue(self, repo: str, issue: Issue) -> None: ...
    def put_pull(self, repo: str, pull: PullRequest) -> None: ...
    def next_comment_id(self, repo: str) -> int: ...

    def add_issue_comment(self, repo: str, number: int, comment: Comment) -> None: ...
    def add_pull_comment(self, repo: str, number: int, comment: Comment) -> None: ...
