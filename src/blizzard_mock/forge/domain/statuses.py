"""Commit status history and scoped read controls."""

from typing import Literal, Protocol

from pydantic import BaseModel


class StatusSpec(BaseModel):
    context: str
    state: Literal["error", "failure", "pending", "success"]
    description: str | None = None
    target_url: str | None = None


class Status(StatusSpec):
    id: int


def combined_status(history: list[Status]) -> tuple[str, list[Status]]:
    latest: dict[str, Status] = {}
    for status in history:
        latest.setdefault(status.context, status)
    statuses = list(latest.values())
    states = [status.state for status in statuses]
    if any(state in ("failure", "error") for state in states):
        return "failure", statuses
    if not states or "pending" in states:
        return "pending", statuses
    return "success", statuses


class IStatusStore(Protocol):
    def history(self, repo: str, sha: str) -> list[Status]: ...
    def replace(self, repo: str, sha: str, statuses: list[StatusSpec]) -> None: ...
    def read_error(self, repo: str, sha: str) -> int | None: ...
    def set_read_error(self, repo: str, sha: str, status: int | None) -> None: ...
    def clear_all(self) -> None: ...
