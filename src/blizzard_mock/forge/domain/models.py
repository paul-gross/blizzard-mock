"""Forge domain types — the vendor-neutral shape of the state the forge holds.

These are the entities the business rules operate on. They deliberately carry
no GitHub wire fields (``url``, ``html_url``, nested ``user`` objects), which
keeps the merge/mergeability rules unit-testable without an HTTP app.
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
    #: Base advanced with no conflict, merely out of date; cleared via
    #: ``PUT .../update-branch``. A conflicting stale branch is ``DIRTY``, not this.
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

    Issue and pull-request numbers share one per-repo counter, mirroring GitHub.
    """

    number: int
    title: str
    body: str
    state: State = State.OPEN
    state_reason: str | None = None
    user: str
    labels: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    comments: list[Comment] = Field(default_factory=list)


class Label(BaseModel):
    """A repo-level label definition (name only — no color/description modeled)."""

    name: str


class CheckRun(BaseModel):
    """One check run against a commit — a CI job's status/conclusion.

    Vendor-neutral; ``conclusion`` is ``None`` until ``status`` is ``"completed"``.
    """

    id: int
    name: str
    status: str
    conclusion: str | None = None
    head_sha: str


class PullRequest(BaseModel):
    """A delivery item: a merge proposal of ``head`` into ``base`` (D-057…D-065).

    ``head``/``base`` are branch names in the bare repo; merging is real.
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
