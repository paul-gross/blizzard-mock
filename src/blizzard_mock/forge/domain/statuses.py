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


class IStatusStore(Protocol):
    def history(self, repo: str, sha: str) -> list[Status]: ...
    def replace(self, repo: str, sha: str, statuses: list[StatusSpec]) -> None: ...
    def read_error(self, repo: str, sha: str) -> int | None: ...
    def set_read_error(self, repo: str, sha: str, status: int | None) -> None: ...
    def clear_all(self) -> None: ...
