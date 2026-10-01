"""Read/write git backend Protocols over bare repos.

Mergeability uses real refs; merges update the base branch
(``bzh:repository-split`` / ``bzh:dependency-inversion``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard_mock.forge.domain.models import Repo


@dataclass(frozen=True)
class GitAuthor:
    name: str
    email: str
    date: str  # ISO-8601, as git records it


@dataclass(frozen=True)
class GitCommit:
    sha: str
    message: str
    author: GitAuthor
    parents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitCompareCommit:
    sha: str
    parents: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GitCompareFile:
    filename: str
    status: str  # GitHub's vocabulary: added/removed/modified/renamed/copied/changed
    sha: str  # blob sha at head; at the merge base for a removed file
    patch: str | None  # None for a binary change
    previous_filename: str | None = None


@dataclass(frozen=True)
class GitCompare:
    status: str  # identical/ahead/behind/diverged
    ahead_by: int
    behind_by: int
    merge_base_sha: str
    commits: list[GitCompareCommit]
    files: list[GitCompareFile]  # diff of merge base against head


class IReadGitBackend(Protocol):
    def get_repo(self, owner: str, name: str) -> Repo:
        """Resolve ``owner/name`` to a backing bare repo, reading its default
        branch from ``HEAD``. Raises ``RepoNotFound`` when none backs it."""
        ...

    def branch_exists(self, repo: Repo, branch: str) -> bool: ...

    def resolve_ref(self, repo: Repo, ref: str) -> str:
        """Resolve a branch/ref/sha to its full commit sha. Raises
        ``BranchNotFound`` when it does not resolve."""
        ...

    def get_commit(self, repo: Repo, ref: str) -> GitCommit: ...

    def compare(self, repo: Repo, base: str, head: str) -> GitCompare:
        """Compare ``base...head``. Raises ``BranchNotFound`` when either side
        does not resolve."""
        ...

    def is_mergeable(self, repo: Repo, base: str, head: str) -> bool:
        """True when ``head`` merges into ``base`` with no conflict, computed
        against real refs (``git merge-tree``)."""
        ...

    def is_ancestor(self, repo: Repo, ancestor: str, descendant: str) -> bool:
        """True when ``ancestor`` is reachable from ``descendant`` — used to
        assert a landed commit is reachable from the base branch."""
        ...


class IWriteGitBackend(IReadGitBackend, Protocol):
    def merge(self, repo: Repo, base: str, head: str, message: str) -> str:
        """Really merge ``head`` into ``base`` in the bare repo and return the
        new commit sha on ``base``. Raises ``NotMergeable`` on real conflict."""
        ...

    def rebase(self, repo: Repo, base: str, head: str, message: str) -> str:
        """Replay ``head``'s commits onto ``base`` as new commits and advance
        ``base`` to the new tip, leaving ``head`` untouched — GitHub's
        rebase-merge. Raises ``NotMergeable`` on real conflict."""
        ...

    def update_ref(self, repo: Repo, ref: str, sha: str) -> None:
        """Set ``refs/heads/<ref>`` to point at ``sha`` unconditionally — the
        raw ref write behind the domain's fast-forward compare-and-swap."""
        ...
