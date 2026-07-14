"""The mock-hub state seam — chunk rows and the runner registry.

Git-truth-free: unlike the forge, the hub mock is pure in-memory state (the real hub is
too, modulo its sqlite facts). Split read/write per ``bzh:repository-split`` is overkill
for one in-process map, so this is a single seam the ``MockHubService`` writes through and
the ``internal`` adapter implements; the routers never touch it (``bzh:controller-read-only``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard_mock.mock_hub.domain.models import ChunkState


class RunnerRow:
    """A registered runner's mutable registry row (liveness + pause brake)."""

    def __init__(self, runner_id: str, workspace_id: str, at: datetime) -> None:
        self.runner_id = runner_id
        self.workspace_id = workspace_id
        self.registered_at = at
        self.last_seen_at = at
        self.paused = False


class IHubState(Protocol):
    """The mock hub's write-through state: chunks and the runner registry."""

    def put_chunk(self, chunk: ChunkState) -> None: ...
    def get_chunk(self, chunk_id: str) -> ChunkState | None: ...
    def list_chunks(self) -> list[ChunkState]: ...
    def upsert_runner(self, runner_id: str, workspace_id: str, at: datetime) -> bool:
        """Register/heartbeat a runner; return ``True`` on first registration."""
        ...

    def get_runner(self, runner_id: str) -> RunnerRow | None: ...
    def list_runners(self) -> list[RunnerRow]: ...
    def clear(self) -> None: ...
