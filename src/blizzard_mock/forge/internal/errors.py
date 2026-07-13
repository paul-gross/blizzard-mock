"""The injected git-error factory — wraps GitPython failures once, at ERROR.

Follows the factory-injected error pattern (``exemplars/python/repo_pattern.py``,
``standards/logging.md``): library exceptions become domain ``GitError`` /
``NotMergeable`` at the boundary, logged exactly once here with structured
fields, so callers never re-log and no catch-log-rethrow cascade forms.
"""

from __future__ import annotations

import structlog

from blizzard_mock.forge.domain.errors import GitError, NotMergeable


class GitErrorFactory:
    """Translates GitPython/subprocess git failures into domain errors."""

    def __init__(self, log: structlog.stdlib.BoundLogger) -> None:
        self._log = log

    def from_git(self, exc: Exception, message: str, *, repo: str = "", op: str = "") -> GitError:
        detail = str(exc).strip()
        self._log.error(message, repo=repo, op=op, detail=detail)
        return GitError(message)

    def conflict(self, message: str, *, repo: str = "", op: str = "") -> NotMergeable:
        self._log.warning(message, repo=repo, op=op)
        return NotMergeable(message)
