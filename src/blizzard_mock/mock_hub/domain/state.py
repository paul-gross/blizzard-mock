"""The mock-hub state seam — chunk rows and the runner registry.

Split read/write per ``bzh:repository-split`` is overkill for one in-process map, so this
is a single seam the ``MockHubService`` writes through and the ``internal`` adapter
implements; the routers never touch it (``bzh:controller-read-only``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard_mock.mock_hub.domain.models import ChunkState, QuestionState
from blizzard_mock.mock_hub.domain.wire import SubscriptionUsageView


@dataclass(frozen=True)
class RunnerCapability:
    """One harness binding a registered runner reported it can execute —
    the mock's own domain-core mirror of the wire shape, kept import-free of it. ``version``
    is ``None`` when absent; ``default`` marks the runner's own default binding. ``default``/
    ``available`` are keyword-only so two adjacent bools can't swap silently unkeyworded."""

    harness_id: str
    version: str | None = None
    tiers: tuple[str, ...] = ()
    default: bool = field(default=False, kw_only=True)
    available: bool = field(default=True, kw_only=True)


@dataclass(frozen=True, kw_only=True)
class DeclaredSubscription:
    """One provider subscription a registered runner declared — the mock's own
    domain-core mirror of the wire shape. Its slug is the roster's own membership key: a
    declared slug is a member whatever its sample's age, a dropped one is not, even
    though its reported rows persist. Keyword-only against a silent positional swap."""

    slug: str
    name: str
    provider: str


@dataclass(frozen=True, kw_only=True)
class SubscriptionUsageMiss:
    """One declared subscription's newest reported miss — a sibling to
    :class:`SubscriptionUsageView`, held per slug and never overwriting the sample it is unioned
    with at read time; mirrors the real hub's ``runner_external_usage_misses`` row. Keyword-only:
    ``slug``/``name``/``reason`` share one type, and a positional call could swap them silently."""

    slug: str
    name: str
    missed_at: datetime
    reason: str


class ReportedRunnerFacts:
    """What a runner reports *about itself*, held per ``runner_id`` and never gated on a
    registration: the outbound buffer replays an outage in FIFO order, so one of these can
    legitimately arrive before the registration that follows it, and must be readable once
    it does (mirrors the real hub's ``record_local_pause``/``record_external_usage``)."""

    def __init__(self) -> None:
        # The runner's own locally-reported pause brake — distinct from ``RunnerRow.paused``,
        # the fleet's brake; reported-up, read-only.
        self.locally_paused = False
        self.locally_paused_by: str | None = None
        self.locally_paused_reason: str | None = None
        # Every declared subscription's newest sample, keyed by slug —
        # a slug absent here has never reported.
        self.subscription_usage: dict[str, SubscriptionUsageView] = {}
        # Every declared subscription's newest reported miss, keyed by slug —
        # a sibling to `subscription_usage`, never overwriting a sample; unioned at read time.
        self.subscription_usage_misses: dict[str, SubscriptionUsageMiss] = {}


@dataclass(frozen=True)
class ScopeRow:
    """One seeded scope — global vocabulary, mirrors the real hub's minted ``Scope``.

    ``description``/``created_at`` are keyword-only: two adjacent ``str`` positionals
    swap silently at a call site otherwise."""

    slug: str
    description: str = field(kw_only=True)
    created_at: str = field(kw_only=True)
    retired: bool = False


class RunnerRow:
    """An added runner's mutable registry row. Mirrors the real hub's ``runner_registrations`` row.

    ``add`` writes the row never connected; a registration fills the rest. What the runner reports about
    itself lives in :class:`ReportedRunnerFacts`, which outlives and predates this row."""

    def __init__(self, runner_id: str, *, name: str, added_at: datetime) -> None:
        self.runner_id = runner_id
        # Display only and not unique — the runner owns it, and every registration may replace it.
        self.name = name
        self.added_at = added_at
        self.workspace_id: str | None = None
        self.registered_at: datetime | None = None
        self.last_seen_at: datetime | None = None
        # The runner's optional federation identity — reported on every registration.
        self.url: str | None = None
        self.redirect_uris: tuple[str, ...] = ()
        self.env_capacity: int | None = None
        # The runner's capability snapshot — reported on every registration, replacing the
        # prior snapshot whole.
        self.capabilities: tuple[RunnerCapability, ...] = ()
        # The runner's declared subscription roster, kept distinct from an absent one;
        # replaced whole on every registration, the same way `capabilities` is.
        self.declared_subscriptions: tuple[DeclaredSubscription, ...] | None = None
        # The node names the runner holds for a human decision, replaced whole on every registration.
        self.gates: tuple[str, ...] = ()
        self.paused = False
        self.retired_at: datetime | None = None
        self.retired_by: str | None = None

    def never_connected(self) -> bool:
        """Added but never registered — the hub knows its id and name, and nothing it reports."""
        return self.registered_at is None


class ClaimDeniedUnregistered(Exception):
    """The claiming runner holds no registration — refused before any race. A live runner
    re-registers every tick, so the next tick's claim follows a registration."""

    def __init__(self, *, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is not registered at the hub")
        self.runner_id = runner_id


class ClaimDeniedPaused(Exception):
    """The claiming runner is paused at the hub registry — refused before any race."""

    def __init__(self, *, runner_id: str) -> None:
        super().__init__(f"runner {runner_id} is paused at the hub")
        self.runner_id = runner_id


def refuse_braked_runner(row: RunnerRow | None, *, runner_id: str) -> RunnerRow:
    """Refuse a claim from a runner unregistered — never added, or added but never connected —
    (:class:`ClaimDeniedUnregistered`) or paused at the hub registry (:class:`ClaimDeniedPaused`);
    the registration the claim stands on otherwise."""
    if row is None or row.never_connected():
        raise ClaimDeniedUnregistered(runner_id=runner_id)
    if row.paused:
        raise ClaimDeniedPaused(runner_id=runner_id)
    return row


class IHubState(Protocol):
    """The mock hub's write-through state: chunks, questions, the runner registry, and the
    global (not per-chunk) published system-artifact set."""

    def put_chunk(self, chunk: ChunkState) -> None: ...
    def get_chunk(self, chunk_id: str) -> ChunkState | None: ...
    def list_chunks(self) -> list[ChunkState]: ...
    def add_runner(self, row: RunnerRow, *, token: str) -> None:
        """Insert a never-connected runner holding ``token`` as its current bearer token."""
        ...

    def record_registration(
        self,
        runner_id: str,
        *,
        name: str | None,
        workspace_id: str,
        at: datetime,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        env_capacity: int | None = None,
        capabilities: tuple[RunnerCapability, ...] = (),
        declared_subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
    ) -> bool:
        """Register/heartbeat an added runner; return ``True`` on its first registration.

        Never inserts — an id no ``add`` wrote raises ``LookupError``. ``name`` replaces the held
        name unless ``None``; everything else is overwritten on every call."""
        ...

    def runner_for_token(self, token: str) -> RunnerRow | None:
        """The runner ``token`` is the current bearer token of, else ``None``."""
        ...

    def revoked_token_runner_id(self, token: str) -> str | None:
        """The runner a revoked or rotated-away ``token`` was issued to, else ``None``."""
        ...

    def replace_token(self, runner_id: str, *, token: str | None) -> None:
        """Make ``token`` the runner's current bearer token (``None``: it holds none); the token it
        held until now is revoked."""
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

    def put_scope(self, row: ScopeRow) -> None:
        """Upsert one seeded scope, global vocabulary like the system-artifact set."""
        ...

    def list_scopes(self) -> list[ScopeRow]:
        """Every seeded scope, newest first — mirrors the real hub's own ``list_all``."""
        ...

    def clear(self) -> None:
        """Drop every row, the runner registry and its tokens included — a hub data reset, after
        which every token a runner holds is unknown here."""
        ...
