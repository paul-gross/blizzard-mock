"""The commit-attached check-run seam.

A check run is normally derived from the levers of the pull whose head resolves to
a revision. The ``ref_checks`` lever instead arms *named* runs against any revision;
this store holds them per ``(repo, sha)``.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel

from blizzard_mock.forge.domain.models import CheckRun

#: The statuses a ``ref_checks`` entry may name, and the (status, conclusion) each reads as.
CHECK_STATUSES: dict[str, tuple[str, str | None]] = {
    "pending": ("in_progress", None),
    "failed": ("completed", "failure"),
    "success": ("completed", "success"),
}


class CheckSpec(BaseModel):
    """One named run in a ``ref_checks`` lever."""

    name: str
    status: str


class ICheckRunStore(Protocol):
    """Named check runs armed against a revision."""

    def runs(self, repo: str, sha: str) -> list[CheckRun] | None:
        """The runs armed against ``sha`` — ``None`` when nothing was armed, an empty
        list when "no runs" was armed explicitly."""
        ...

    def replace_all(self, repo: str, sha: str, runs: list[CheckRun]) -> None:
        """Arm exactly ``runs`` against ``sha`` (an empty list arms "no runs")."""
        ...

    def put(self, repo: str, sha: str, run: CheckRun) -> None:
        """Add ``run`` beside the runs already there, replacing a run of the same name."""
        ...

    def clear_all(self) -> None: ...
