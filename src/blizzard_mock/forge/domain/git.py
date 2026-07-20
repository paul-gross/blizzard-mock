"""The git backend seam — the single git truth behind the forge.

The forge's backing model is a directory of **bare git repos** (the same
``file://`` origins the fixture workspace pushes to). Mergeability is computed
against real refs, and a merge performs a real merge into the bare repo's base
branch. The domain declares this seam as read/write Protocols
(``bzh:repository-split`` / ``bzh:dependency-inversion``); the GitPython adapter
in ``forge.internal.git_backend`` implements it. Domain code depends on the
narrowest Protocol its job needs — the merge rules hold the write variant, the
read routes the read-only one (``bzh:controller-read-only``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from blizzard_mock.forge.domain.models import Repo


@dataclass(frozen=True)
class GitAuthor:
    """Authorship of a commit."""

    name: str
    email: str
    date: str  # ISO-8601, as git records it


@dataclass(frozen=True)
class GitCommit:
    """A commit resolved from the bare repo."""

    sha: str
    message: str
    author: GitAuthor
    parents: list[str] = field(default_factory=list)


class IReadGitBackend(Protocol):
    """Read-only operations over the directory of bare repos.

    Controllers (the read routes) and the mergeability check depend on this."""

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

    def is_mergeable(self, repo: Repo, base: str, head: str) -> bool:
        """True when ``head`` merges into ``base`` with no conflict, computed
        against real refs (``git merge-tree``)."""
        ...

    def is_ancestor(self, repo: Repo, ancestor: str, descendant: str) -> bool:
        """True when ``ancestor`` is reachable from ``descendant`` — used to
        assert a landed commit is reachable from the base branch."""
        ...


class IWriteGitBackend(IReadGitBackend, Protocol):
    """Read-write variant. Only the domain (``ForgeService``) depends on this."""

    def merge(self, repo: Repo, base: str, head: str, message: str) -> str:
        """Really merge ``head`` into ``base`` in the bare repo and return the
        new commit sha on ``base``. Raises ``NotMergeable`` on real conflict."""
        ...

    def update_ref(self, repo: Repo, ref: str, sha: str) -> None:
        """Set ``refs/heads/<ref>`` to point at ``sha`` unconditionally — the
        raw ref write behind the domain's fast-forward compare-and-swap."""
        ...
