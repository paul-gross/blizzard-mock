"""Forge domain types — the vendor-neutral shape of the state the forge holds.

These are the entities the business rules operate on. They deliberately do *not*
carry GitHub wire fields (``url``, ``html_url``, nested ``user`` objects); the
``forge.api.serialization`` layer renders these into GitHub-shaped JSON. Keeping
the wire shape out of the domain is what lets the merge/mergeability rules be
unit-tested without an HTTP app (``bzh:domain-core``).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class State(StrEnum):
    """Open/closed lifecycle shared by issues and pull requests."""

    OPEN = "open"
    CLOSED = "closed"


class MergeableState(StrEnum):
    """GitHub's ``mergeable_state`` summary for a pull request."""

    CLEAN = "clean"
    DIRTY = "dirty"
    UNKNOWN = "unknown"
    #: Base advanced with no conflict — the branch is merely out of date. GitHub
    #: clears it via ``PUT .../update-branch``; a *conflicting* stale branch is
    #: ``DIRTY``, never ``BEHIND``.
    BEHIND = "behind"
    #: Content-mergeable but held by branch protection — required checks/reviews
    #: not yet green (the "CI isn't green yet" wait state).
    BLOCKED = "blocked"


class MergeMethod(StrEnum):
    """The merge strategy requested at ``PUT .../merge``."""

    MERGE = "merge"
    SQUASH = "squash"
    REBASE = "rebase"


class Repo(BaseModel):
    """A repository the forge fronts — backed by a bare git repo on disk."""

    owner: str
    name: str
    default_branch: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"


class Comment(BaseModel):
    """One entry in an issue or pull-request comment thread."""

    id: int
    body: str
    user: str
    created_at: datetime
    updated_at: datetime


class Issue(BaseModel):
    """A work-source item: title, body, and a comment thread (D-047 / D-074).

    Issue and pull-request numbers share one per-repo counter, mirroring
    GitHub — a pull request is also addressable as an issue.
    """

    number: int
    title: str
    body: str
    state: State = State.OPEN
    user: str
    labels: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    comments: list[Comment] = Field(default_factory=list)


class PullRequest(BaseModel):
    """A delivery item: a merge proposal of ``head`` into ``base`` (D-057…D-065).

    ``head`` / ``base`` are branch names in the backing bare repo; mergeability
    and the merge itself are computed/performed against those real refs. The
    merge-outcome fields (``merged``, ``merge_commit_sha``, …) are forge state
    set when a merge lands — via the merge route or the external-merge lever.
    """

    number: int
    title: str
    body: str
    state: State = State.OPEN
    user: str
    head: str
    base: str
    created_at: datetime
    updated_at: datetime
    comments: list[Comment] = Field(default_factory=list)
    merged: bool = False
    merged_at: datetime | None = None
    merged_by: str | None = None
    merge_commit_sha: str | None = None
