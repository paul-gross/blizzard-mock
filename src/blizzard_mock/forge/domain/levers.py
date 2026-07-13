"""Levers — the forge's first-class edge-state controls.

A lever is an explicit control a test or agent pulls to steer the forge into a
named state instead of contriving it (``implementation/mocking.md``). Two shapes:

* **State levers** persist until cleared and bend later requests — ``merge_conflict``,
  ``merge_rejected`` (per pull request); ``rate_limited``, ``token_rejected``,
  ``unreachable`` (request-scoped, per-repo or global). The API middleware and
  the merge rules consult the active set.
* **Action levers** fire once and mutate state immediately — ``externally_merged``
  performs a real merge into ``base`` outside the PR flow (the direct push to
  ``main`` a delivery flow must detect, D-065); ``comment_midflight`` appends a
  comment to a live thread (D-074).

Scope is ``(repo full name, number)`` where meaningful; a lever with neither is
global. This module holds only the vocabulary and the store seam — the git and
state effects live in ``ForgeService`` and the middleware.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field


class LeverKind(StrEnum):
    """The named states the forge can be steered into."""

    EXTERNALLY_MERGED = "externally_merged"
    MERGE_CONFLICT = "merge_conflict"
    MERGE_REJECTED = "merge_rejected"
    COMMENT_MIDFLIGHT = "comment_midflight"
    RATE_LIMITED = "rate_limited"
    TOKEN_REJECTED = "token_rejected"
    UNREACHABLE = "unreachable"


#: Levers that persist as request-bending state until explicitly cleared.
STATE_LEVERS: frozenset[LeverKind] = frozenset(
    {
        LeverKind.MERGE_CONFLICT,
        LeverKind.MERGE_REJECTED,
        LeverKind.RATE_LIMITED,
        LeverKind.TOKEN_REJECTED,
        LeverKind.UNREACHABLE,
    }
)

#: Levers that fire once and mutate git/thread state immediately.
ACTION_LEVERS: frozenset[LeverKind] = frozenset(
    {
        LeverKind.EXTERNALLY_MERGED,
        LeverKind.COMMENT_MIDFLIGHT,
    }
)


class Lever(BaseModel):
    """One armed state lever, scoped and optionally self-expiring."""

    kind: LeverKind
    repo: str | None = None
    number: int | None = None
    #: ``rate_limited`` may auto-clear after this many affected requests; ``None``
    #: means sticky until cleared.
    remaining: int | None = None
    #: Free-form detail surfaced in the error body (e.g. a rejection reason).
    message: str | None = None

    def scope_key(self) -> str:
        return _scope_key(self.repo, self.number)

    def matches(self, repo: str | None, number: int | None) -> bool:
        """A global lever (no repo) matches everything; a repo-scoped lever
        matches that repo; a PR-scoped lever matches that repo and number."""
        if self.repo is not None and self.repo != repo:
            return False
        return not (self.number is not None and self.number != number)


class LeverParams(BaseModel):
    """Request body for arming a lever / firing an action lever."""

    repo: str | None = None
    number: int | None = None
    remaining: int | None = None
    message: str | None = None
    #: ``comment_midflight`` body and author.
    body: str | None = None
    user: str = Field(default="octocat")


def _scope_key(repo: str | None, number: int | None) -> str:
    return f"{repo or '*'}#{number if number is not None else '*'}"


class ILeverStore(Protocol):
    """Read/write seam over the active state-lever set (one process-wide store)."""

    def arm(self, lever: Lever) -> None: ...
    def clear(self, kind: LeverKind, repo: str | None, number: int | None) -> None: ...
    def clear_all(self) -> None: ...
    def active(self) -> list[Lever]: ...
    def find(self, kind: LeverKind, repo: str | None, number: int | None) -> Lever | None: ...
    def consume(self, lever: Lever) -> None:
        """Decrement a self-expiring lever's ``remaining``, clearing it at zero."""
        ...
