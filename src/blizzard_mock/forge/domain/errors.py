"""Domain errors the forge raises, each carrying the HTTP status GitHub returns.

The API layer owns one exception handler that renders any ``ForgeError`` into a
GitHub-shaped ``{"message": ...}`` body at ``error.status``. Domain and adapter
code raise these; nothing catches a raw GitPython or filesystem exception past
the boundary that wraps it (``forge.internal.errors``).
"""

from __future__ import annotations


class ForgeError(Exception):
    """Base class for every forge failure that maps to an HTTP response."""

    status: int = 500

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RepoNotFound(ForgeError):
    """No bare repo backs the requested ``owner/name`` (GitHub 404)."""

    status = 404


class IssueNotFound(ForgeError):
    """No issue with the requested number in the repo (GitHub 404)."""

    status = 404


class PullNotFound(ForgeError):
    """No pull request with the requested number in the repo (GitHub 404)."""

    status = 404


class BranchNotFound(ForgeError):
    """A referenced branch/ref does not exist in the bare repo (GitHub 404/422)."""

    status = 422


class NotFastForward(ForgeError):
    """A non-force ref update was not a fast-forward — the current ref sha is
    not an ancestor of the target sha (GitHub 422, ``Update is not a fast
    forward``)."""

    status = 422


class ValidationError(ForgeError):
    """A malformed create request — missing head/base, bad state (GitHub 422)."""

    status = 422


class NotMergeable(ForgeError):
    """The pull request cannot be merged — real conflict or ``merge_conflict``
    lever (GitHub 405, ``Pull Request is not mergeable``)."""

    status = 405


class MergeRejected(ForgeError):
    """The merge was refused by policy — the ``merge_rejected`` lever, standing
    in for branch protection / required checks (GitHub 405)."""

    status = 405


class HeadMismatch(ForgeError):
    """The merge's ``sha`` guard did not match the current head — the head branch
    moved since the caller last read it (GitHub 409, ``Head branch was
    modified``)."""

    status = 409


class GitError(ForgeError):
    """An operation against the backing git repo failed unexpectedly."""

    status = 500
