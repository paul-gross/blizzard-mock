"""Levers — the forge's first-class edge-state controls.

A lever is an explicit control a test or agent pulls to steer the forge into
a named state. **State levers** persist until cleared; **action levers** fire
once and mutate state immediately.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, Field

from blizzard_mock.forge.domain.checks import CheckSpec
from blizzard_mock.forge.domain.models import MergeMethod


class LeverKind(StrEnum):
    """The named states the forge can be steered into."""

    EXTERNALLY_MERGED = "externally_merged"
    MERGE_CONFLICT = "merge_conflict"
    MERGE_REJECTED = "merge_rejected"
    COMMENT_MIDFLIGHT = "comment_midflight"
    RATE_LIMITED = "rate_limited"
    TOKEN_REJECTED = "token_rejected"
    UNREACHABLE = "unreachable"
    #: Forces ``mergeable_state: behind`` — base moved, no conflict; cleared by
    #: ``update-branch``, which also advances the head (self-heal path).
    STALE_BRANCH = "stale_branch"
    #: Forces ``mergeable_state: blocked`` — required checks/reviews not green yet;
    #: cleared explicitly to stand in for "CI went green".
    CHECKS_PENDING = "checks_pending"
    #: Per PR: the head carries one completed/failure check run; also forces
    #: ``mergeable_state: blocked`` — an armed PR is never green with a red check.
    CHECKS_FAILED = "checks_failed"
    #: Per repo: the base branch's own latest check run is completed/failure —
    #: "the base gate was already red", independent of any PR.
    BASE_CHECKS_FAILED = "base_checks_failed"
    #: Action: arms named check runs against any revision or branch, each ``pending``,
    #: ``failed``, or ``success``, or arms none at all.
    REF_CHECKS = "ref_checks"
    #: Per repo and branch: ``PATCH git/refs`` on the branch is refused as protected.
    BRANCH_PROTECTED = "branch_protected"
    #: Per repo and glob pattern: ``POST git/refs`` and ``POST /merges`` on a matching
    #: branch are refused by a repository rule.
    RULESET = "ruleset"
    #: Per repo: ``PUT .../merge`` refuses the ``merge`` method.
    MERGE_COMMITS_DISALLOWED = "merge_commits_disallowed"
    #: Action: sets the remaining and the reset of one token's rate window, or of every
    #: token's.
    RATE_BUDGET = "rate_budget"
    #: Global/repo: requests → 403 secondary rate limit, the bucket's remaining untouched.
    SECONDARY_RATE_LIMITED = "secondary_rate_limited"


#: Levers that persist as request-bending state until explicitly cleared.
STATE_LEVERS: frozenset[LeverKind] = frozenset(
    {
        LeverKind.MERGE_CONFLICT,
        LeverKind.MERGE_REJECTED,
        LeverKind.RATE_LIMITED,
        LeverKind.TOKEN_REJECTED,
        LeverKind.UNREACHABLE,
        LeverKind.STALE_BRANCH,
        LeverKind.CHECKS_PENDING,
        LeverKind.CHECKS_FAILED,
        LeverKind.BASE_CHECKS_FAILED,
        LeverKind.BRANCH_PROTECTED,
        LeverKind.RULESET,
        LeverKind.MERGE_COMMITS_DISALLOWED,
        LeverKind.SECONDARY_RATE_LIMITED,
    }
)

#: Levers that fire once and mutate git/thread state immediately.
ACTION_LEVERS: frozenset[LeverKind] = frozenset(
    {
        LeverKind.EXTERNALLY_MERGED,
        LeverKind.COMMENT_MIDFLIGHT,
        LeverKind.REF_CHECKS,
        LeverKind.RATE_BUDGET,
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
    #: ``branch_protected``: the protected branch. Part of the scope, so several branches
    #: of one repo can be protected at once.
    branch: str | None = None
    #: ``ruleset``: the glob over branch names the rule covers. Part of the scope.
    pattern: str | None = None

    def scope_key(self) -> str:
        return _scope_key(self.repo, self.number, self.branch, self.pattern)

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
    #: ``externally_merged``: the merge method the person used; ``merge`` when absent.
    method: MergeMethod | None = None
    #: ``ref_checks``: the branch or revision, and the named runs armed against it.
    ref: str | None = None
    checks: list[CheckSpec] | None = None
    #: ``branch_protected`` / ``ruleset``: the covered branch and glob pattern.
    branch: str | None = None
    pattern: str | None = None
    #: ``rate_budget``: the credential addressed (every one when absent), the remaining
    #: to set, and the reset as epoch seconds — the same unit as ``X-RateLimit-Reset``.
    token: str | None = None
    reset: int | None = None


def _scope_key(repo: str | None, number: int | None, branch: str | None = None, pattern: str | None = None) -> str:
    key = f"{repo or '*'}#{number if number is not None else '*'}"
    if branch is not None:
        key += f"@{branch}"
    if pattern is not None:
        key += f"~{pattern}"
    return key


class ILeverStore(Protocol):
    """Read/write seam over the active state-lever set (one process-wide store)."""

    def arm(self, lever: Lever) -> None: ...
    def clear(
        self,
        kind: LeverKind,
        repo: str | None,
        number: int | None,
        branch: str | None = None,
        pattern: str | None = None,
    ) -> None: ...
    def clear_all(self) -> None: ...
    def active(self) -> list[Lever]: ...
    def find(self, kind: LeverKind, repo: str | None, number: int | None) -> Lever | None: ...
    def consume(self, lever: Lever) -> None:
        """Decrement a self-expiring lever's ``remaining``, clearing it at zero."""
        ...
