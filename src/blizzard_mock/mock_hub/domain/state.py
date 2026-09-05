"""The mock-hub state seam — chunk rows and the runner registry.

Split read/write per ``bzh:repository-split`` is overkill for one in-process map, so this
is a single seam the ``MockHubService`` writes through and the ``internal`` adapter
implements; the routers never touch it (``bzh:controller-read-only``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState
from blizzard_mock.mock_hub.domain.wire import SubscriptionUsageView


class ReportedRunnerFacts:
    """What a runner reports *about itself*, held per ``runner_id`` and never gated on a
    registration: the outbound buffer replays an outage in FIFO order, so one of these can
    legitimately arrive before the registration that follows it, and must be readable once
    it does (mirrors the real hub's ``record_local_pause``/``record_external_usage``)."""

    def __init__(self) -> None:
        # The runner's own locally-reported pause brake — distinct from ``RunnerRow.paused``,
        # the fleet's brake (issue #43/#44); reported-up, read-only.
        self.locally_paused = False
        self.locally_paused_by: str | None = None
        self.locally_paused_reason: str | None = None
        # Every declared subscription's newest sample, keyed by slug (issue #218,
        # blizzard#436) — a slug absent here has never reported.
        self.subscription_usage: dict[str, SubscriptionUsageView] = {}


class RunnerRow:
    """A registered runner's mutable registry row (liveness + the fleet's brake).

    Carries only what a registration writes. What the runner reports about itself lives in
    :class:`ReportedRunnerFacts`, which outlives and predates this row."""

    def __init__(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        at: datetime,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        env_capacity: int | None = None,
    ) -> None:
        self.runner_id = runner_id
        self.workspace_id = workspace_id
        self.registered_at = at
        self.last_seen_at = at
        # The runner's optional federation identity (issue #95) — reported on every
        # (re-)registration.
        self.url = url
        self.redirect_uris = redirect_uris
        self.env_capacity = env_capacity
        self.paused = False


class IHubState(Protocol):
    """The mock hub's write-through state: chunks, questions, the runner registry, and the
    global (not per-chunk) published system-artifact set."""

    def put_chunk(self, chunk: ChunkState) -> None: ...
    def get_chunk(self, chunk_id: str) -> ChunkState | None: ...
    def list_chunks(self) -> list[ChunkState]: ...
    def upsert_runner(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        at: datetime,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        env_capacity: int | None = None,
    ) -> bool:
        """Register/heartbeat a runner; return ``True`` on first registration.

        ``url``/``redirect_uris`` (issue #95) and ``env_capacity`` are overwritten
        unconditionally on every call, like ``workspace_id``."""
        ...

    def get_runner(self, runner_id: str) -> RunnerRow | None: ...
    def list_runners(self) -> list[RunnerRow]: ...

    def reported_facts(self, runner_id: str) -> ReportedRunnerFacts:
        """This runner's self-reported facts, minting an empty set on first touch — never
        ``None``, because a report for an unregistered runner lands rather than being lost."""
        ...

    def put_question(self, question: QuestionState) -> None: ...
    def get_question(self, question_id: str) -> QuestionState | None: ...
    def list_questions(self) -> list[QuestionState]: ...

    def put_system_artifact(self, name: str, *, content: str) -> None:
        """Upsert one published ``ArtifactScope.SYSTEM`` document, global rather than
        per-chunk (mirrors the hub's own packaged set)."""
        ...

    def get_system_artifact(self, name: str) -> str | None: ...
    def list_system_artifacts(self) -> list[tuple[str, str]]: ...

    def clear(self) -> None: ...
