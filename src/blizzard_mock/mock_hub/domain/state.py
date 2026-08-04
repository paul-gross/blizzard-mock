"""The mock-hub state seam — chunk rows and the runner registry.

Split read/write per ``bzh:repository-split`` is overkill for one in-process map, so this
is a single seam the ``MockHubService`` writes through and the ``internal`` adapter
implements; the routers never touch it (``bzh:controller-read-only``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState


class RunnerRow:
    """A registered runner's mutable registry row (liveness + pause brakes)."""

    def __init__(
        self,
        runner_id: str,
        workspace_id: str,
        at: datetime,
        *,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
    ) -> None:
        self.runner_id = runner_id
        self.workspace_id = workspace_id
        self.registered_at = at
        self.last_seen_at = at
        # The runner's optional federation identity (issue #95) — reported on every
        # (re-)registration (``blizzard.hub.domain.registry``).
        self.url = url
        self.redirect_uris = redirect_uris
        self.paused = False
        # The runner's own locally-reported pause brake (``runner.locally_paused`` /
        # ``runner.locally_resumed``) — distinct from ``paused``, the fleet's brake the
        # runner pulls down (blizzard#43/#44). The mock only enforces the fleet's own
        # brake; this trio is reported-up state, mirrored read-only via ``RunnerView``.
        self.locally_paused = False
        self.locally_paused_by: str | None = None
        self.locally_paused_reason: str | None = None


class IHubState(Protocol):
    """The mock hub's write-through state: chunks, questions, and the runner registry."""

    def put_chunk(self, chunk: ChunkState) -> None: ...
    def get_chunk(self, chunk_id: str) -> ChunkState | None: ...
    def list_chunks(self) -> list[ChunkState]: ...
    def upsert_runner(
        self,
        runner_id: str,
        workspace_id: str,
        at: datetime,
        *,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
    ) -> bool:
        """Register/heartbeat a runner; return ``True`` on first registration.

        ``url``/``redirect_uris`` (issue #95) are overwritten unconditionally on every
        call, like ``workspace_id``."""
        ...

    def get_runner(self, runner_id: str) -> RunnerRow | None: ...
    def list_runners(self) -> list[RunnerRow]: ...
    def put_question(self, question: QuestionState) -> None: ...
    def get_question(self, question_id: str) -> QuestionState | None: ...
    def list_questions(self) -> list[QuestionState]: ...
    def clear(self) -> None: ...
