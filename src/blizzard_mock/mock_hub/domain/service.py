"""``MockHubService`` — the mock hub's business rules over its state and levers.

The domain layer (``bzh:domain-core``): advances a seeded chunk through its
scripted graph exactly as the real hub would. Levers shaping a response body
are consulted here; transport-edge levers live in the middleware.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from blizzard_mock.clock import Clock
from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_hub.domain import matching
from blizzard_mock.mock_hub.domain.levers import HubLever
from blizzard_mock.mock_hub.domain.models import (
    PAUSABLE_STATUSES,
    PRE_CLAIM_STATUSES,
    RESERVED_HUB_SOURCE_NAME,
    TERMINAL,
    TERMINAL_STATUSES,
    AnalyticsCountRowSpec,
    AnalyticsSpendRowSpec,
    ApplyOutcome,
    ChunkSpec,
    ChunkState,
    ChunkStatus,
    EscalationState,
    Executor,
    GardenFindingSpec,
    GardenProposalSpec,
    NodeSpec,
    QuestionState,
    RoutineProposalState,
    ScopeSpec,
    SystemArtifactSpec,
    WorkRefSpec,
    finding_exit,
    ships_from_lease_holder,
    status_if_paused,
)
from blizzard_mock.mock_hub.domain.state import (
    DeclaredSubscription,
    IHubState,
    ReportedRunnerFacts,
    RunnerCapability,
    ScopeRow,
    SubscriptionUsageMiss,
    refuse_braked_runner,
)
from blizzard_mock.mock_hub.domain.wire import (
    AnalyticsCountsResponse,
    AnalyticsCountView,
    AnalyticsSpendResponse,
    AnalyticsSpendView,
    ApplyResponse,
    BlockedView,
    ChunkDetail,
    ChunkEscalationView,
    ChunkStatusView,
    EnvelopeChoice,
    ExternalSubscriptionUsageWindowView,
    FindingView,
    GardenProposalView,
    GraphArtifact,
    HubAdvanceResponse,
    LeaseTranscriptView,
    NodeConfig,
    NodeEnvelope,
    QuestionView,
    QueuePeekEntry,
    QueuePeekResponse,
    RotatePolicyView,
    RouteClaimResponse,
    RouteTokenRekeyResponse,
    RouteView,
    RunnerCapabilityView,
    RunnerFactAck,
    RunnerView,
    ScopeView,
    SubscriptionUsageView,
    SystemArtifactView,
    TranscriptSegmentAck,
    WorkItemEntry,
    WorkItemsView,
)

#: The runner-fact vocabulary the batched ``/events`` push dispatches by kind; agreement
#: with the real vocabulary is asserted by ``tests/test_wire_parity.py``.
LEASE_MINTED = "lease.minted"
ESCALATION_RECORDED = "escalation.recorded"
QUESTION_ASKED = "question.asked"
ANSWER_DELIVERED = "answer.delivered"
RUNNER_LOCALLY_PAUSED = "runner.locally_paused"
RUNNER_LOCALLY_RESUMED = "runner.locally_resumed"
USAGE_RECORDED = "usage.recorded"
EVENT_RECORDED = "event.recorded"
EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED = "external_subscription_usage.sampled"
EXTERNAL_SUBSCRIPTION_USAGE_MISSED = "external_subscription_usage.missed"

#: The one miss reason surfaced as a per-slug `condition` — restated from the real hub, not imported.
_CREDENTIAL_LAPSED_CONDITION = "credential_lapsed"


def _optional_text(value: object) -> str | None:
    return str(value) if value is not None else None


def _work_ref_label(ref: WorkRefSpec) -> str:
    """A work ref's source-native token, rendered the way the real hub's sources render it."""
    return f"hub:{ref.ref}" if ref.source == RESERVED_HUB_SOURCE_NAME else f"{ref.source}#{ref.ref}"


class _ExternalSubscriptionUsageWindowFact(BaseModel):
    """One complete subscription-usage window accepted from a runner fact.

    Mirrors `blizzard.wire.facts.ExternalSubscriptionUsageWindowFact` — restated, not
    imported (no ``blizzard`` dep) — including its per-field strictness, so the mock
    drops exactly the entries the real hub's intake drops. ``resets_at`` stays lax on
    both sides: it arrives as an ISO-8601 string."""

    window: str = Field(strict=True)
    utilization_pct: float = Field(ge=0, le=100, allow_inf_nan=False, strict=True)
    resets_at: datetime
    window_seconds: int = Field(gt=0, strict=True)


#: The real hub's caps, restated not imported (no ``blizzard`` dep); the daily-rate one needs
#: a wall clock. Keep the record cap >= the RUNNER's, or this rejects what the real hub stores.
_TRANSCRIPT_RECORD_MAX_BYTES = 10 * 1024 * 1024
_TRANSCRIPT_CHUNK_BUDGET_MAX_BYTES = 64 * 1024 * 1024


def _finding_view(f: GardenFindingSpec, *, routine_name: str, scope_slug: str) -> FindingView:
    """`f` projected to the real hub's own `FindingView` shape, `routine_name`/
    `scope_slug` supplied by the caller since a `GardenFindingSpec` carries neither
    itself. The one place `test_wire_parity.py` can't scan — keep it the
    only place either read builds this dict."""
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the real hub's own `finding_view` shape.
    return FindingView.model_validate(
        {
            "finding_id": f.finding_id,
            "routine_name": routine_name,
            "scope_slug": scope_slug,
            "class": f.class_,
            "locus": f.locus,
            "summary": f.summary,
            "introduced": f.introduced,
            "introduced_at": f.introduced_at,
            "first_observed_at": f.first_observed_at,
            "live": f.live,
            "state": f.state,
            "note": f.note,
            "last_seen_at": f.last_seen_at,
            "observed_count": f.observed_count,
            "exit": finding_exit(f.state),
        }
    )


def _proposal_view(p: GardenProposalSpec, *, routine_name: str, now: str) -> GardenProposalView:
    """`p` projected to the real hub's own `GardenProposalView` shape, `routine_name`
    supplied by the caller since a `GardenProposalSpec` carries none itself. `now` backs
    `created_at` when the spec left it unseeded (mint-time default), and likewise backs
    a seeded closure's own `closed_at` when that is left unseeded."""
    closure = None
    if p.closure is not None:
        closure = {
            "closure": p.closure.closure,
            "reason": p.closure.reason,
            "closed_by": p.closure.closed_by,
            "closed_at": p.closure.closed_at or now,
            "item_outcome": p.closure.item_outcome,
            "source": p.closure.source,
            "ref": p.closure.ref,
        }
    return GardenProposalView.model_validate(
        {
            "proposal_id": p.proposal_id,
            "origin": "routine-run",
            "routine_name": routine_name,
            "created_by": None,
            "class": p.class_,
            "title": p.title,
            "body": p.body,
            "findings": list(p.findings),
            "created_at": p.created_at or now,
            "closure": closure,
        }
    )


def _counts_response(rows: list[AnalyticsCountRowSpec]) -> AnalyticsCountsResponse:
    return AnalyticsCountsResponse(
        counts=[
            AnalyticsCountView(key=r.key, count=r.count, graph_name=r.graph_name, node_name=r.node_name) for r in rows
        ]
    )


def _spend_response(rows: list[AnalyticsSpendRowSpec]) -> AnalyticsSpendResponse:
    return AnalyticsSpendResponse(
        spend=[
            AnalyticsSpendView(
                key=r.key,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cache_read_tokens=r.cache_read_tokens,
                cache_create_tokens=r.cache_create_tokens,
                cost_usd=r.cost_usd,
                cost_partial=r.cost_partial,
                graph_name=r.graph_name,
                node_name=r.node_name,
            )
            for r in rows
        ]
    )


class ChunkNotFound(Exception):
    """No seeded chunk with that id."""


class QuestionNotFound(Exception):
    """No question with that id."""


class NoRunContext(Exception):
    """The chunk carries no seeded garden run identity — not a routine run."""


class NoAnsweredProposal(Exception):
    """The chunk carries no seeded answered-proposal finding set — mirrors the real hub's
    refusal of a chunk answering no accepted, minted garden proposal."""


class FindingNotInAnsweredSet(Exception):
    """A finding id was asked for by :meth:`MockHubService.answered_finding` but is not
    among the chunk's own seeded answered set."""


class SystemArtifactNotFound(Exception):
    """No published system artifact with that name."""


class UnresolvableRunner(Exception):
    """The matched fleet peek's caller named no ``runner_id``, or one no registration
    knows — mirrors the real hub's ``401`` for an
    unresolvable principal, raised in every mode the mock supports (the mock carries no
    ``warn``/``enforce`` toggle at all, so this is the one check unconditionally on)."""


class DependencyUnmet(Exception):
    """The chunk stands on a prerequisite the ``dependency_unmet`` lever names as not
    ``done`` — the claim is refused with a 409 distinct from :class:`ClaimConflict`."""

    def __init__(self, prerequisite_chunk_id: str) -> None:
        super().__init__(f"chunk depends on unmet prerequisite {prerequisite_chunk_id}")
        self.prerequisite_chunk_id = prerequisite_chunk_id


class ClaimIncompatible(Exception):
    """The claiming runner's *currently stored* capabilities can no longer run every
    statically reachable runner-owned lineage from the chunk's current node — refused
    outright, mirroring :class:`DependencyUnmet`'s shape. A registration
    reporting no capabilities is never checked (see :meth:`MockHubService.claim`)."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"runner {runner_id}'s capabilities no longer satisfy the chunk")
        self.runner_id = runner_id


class MockHubService:
    """The composition-root-wired service every mock-hub route delegates to."""

    def __init__(self, state: IHubState, levers: ILeverStore, clock: Clock) -> None:
        self._state = state
        self._levers = levers
        self._clock = clock
        #: Per-runner fact high-water mark — a seq at/under this mark is
        #: re-acked as ``already_applied`` rather than re-applied.
        self._fact_high_water: dict[str, int] = {}
        #: The transcript lane's own high-water mark — a separate
        #: per-runner sequence from the fact lane's above.
        self._transcript_high_water: dict[str, int] = {}
        #: ``(chunk_id, epoch)`` -> the runner whose ``lease.minted`` first named it.
        self._epoch_owners: dict[tuple[str, int], str] = {}
        #: Accepted bytes per chunk — the chunk-budget cap's running total.
        self._transcript_chunk_bytes: dict[str, int] = {}
        #: The real hub's natural key: a key's own accept/reject decision, independent of
        #: seq, mirroring `IWriteTranscriptSegments.natural_key_state`. Absent reads as "absent".
        self._transcript_key_state: dict[tuple[str, int], str] = {}
        #: Retained transcript records, keyed by lease, then by each record's own
        #: natural key — only accepted records land here, so a capped one never reads back.
        self._transcript_segments: dict[tuple[str, str, int], dict[tuple[str, int], dict[str, Any]]] = {}

    @property
    def levers(self) -> ILeverStore:
        """The active lever store — the ``/_levers`` control surface reads/writes it."""
        return self._levers

    # -- seeding -----------------------------------------------------------

    def seed_chunk(self, spec: ChunkSpec) -> ChunkState:
        """Seed one scripted chunk (POST /_seed/chunk); mint an id if none was given."""
        if spec.entry not in spec.nodes:
            raise ValueError(f"entry node {spec.entry!r} is not in the node set")
        chunk_id = spec.chunk_id or f"ch_{uuid.uuid4().hex[:24]}"
        chunk = ChunkState(
            chunk_id=chunk_id,
            graph_id=spec.graph_id,
            graph_name=spec.graph_name,
            default_model=list(spec.default_model),
            default_effort=spec.default_effort,
            default_harnesses=list(spec.default_harnesses),
            entry=spec.entry,
            nodes=spec.nodes,
            work_refs=spec.work_refs,
            graph_artifacts=spec.graph_artifacts,
            garden_run=spec.garden_run,
            garden_findings=spec.garden_findings,
            garden_answered_findings=spec.garden_answered_findings,
            garden_proposals=spec.garden_proposals,
            analytics=spec.analytics,
            status=spec.status,
        )
        self._state.put_chunk(chunk)
        return chunk

    def reset(self) -> None:
        self._state.clear()
        self._levers.clear_all()
        self._fact_high_water.clear()
        self._transcript_high_water.clear()
        self._epoch_owners.clear()
        self._transcript_chunk_bytes.clear()
        self._transcript_key_state.clear()
        self._transcript_segments.clear()

    # -- system artifacts (ArtifactScope.SYSTEM, global) --------------------

    def seed_system_artifact(self, spec: SystemArtifactSpec) -> None:
        """Publish (or replace) one document (POST /_seed/system-artifacts) — global, not
        tied to any seeded chunk, mirroring the hub's own packaged set."""
        self._state.put_system_artifact(spec.name, content=spec.content)

    def system_artifacts(self) -> list[SystemArtifactView]:
        return [SystemArtifactView(name=name, content=content) for name, content in self._state.list_system_artifacts()]

    def system_artifact(self, name: str) -> SystemArtifactView:
        content = self._state.get_system_artifact(name)
        if content is None:
            raise SystemArtifactNotFound(f"no system artifact {name!r}")
        return SystemArtifactView(name=name, content=content)

    # -- scopes (global vocabulary) ------------------------

    def seed_scope(self, spec: ScopeSpec) -> None:
        """Upsert one scope (``POST /_seed/scopes``) — global, mirrors the real hub's
        mint-on-name vocabulary; a scenario seeds the end state it wants directly rather
        than replaying create/retire."""
        self._state.put_scope(
            ScopeRow(
                slug=spec.slug,
                description=spec.description,
                created_at=spec.created_at or self._clock.now().isoformat(),
                retired=spec.retired,
            )
        )

    def scopes(self) -> list[ScopeView]:
        """Every seeded scope, newest first — mirrors ``GET /api/fleet/scopes``."""
        return [
            ScopeView(slug=row.slug, description=row.description, created_at=row.created_at, retired=row.retired)
            for row in self._state.list_scopes()
        ]

    # -- queue -------------------------------------------------------------

    def peek(self) -> QueuePeekResponse:
        """Ready = seeded, unclaimed, not terminal — FIFO by insertion (D-080).

        Reads the ``dependency_unmet`` lever the same as :meth:`claim` does, but never
        consumes it — the one lever reaches both surfaces, and the peek's
        read must not expire what the later claim still needs to see."""
        ready = [c for c in self._state.list_chunks() if not c.claimed and c.status is ChunkStatus.READY]
        entries = [
            QueuePeekEntry(
                chunk_id=c.chunk_id,
                graph_id=c.graph_id,
                position=i,
                work_refs=[p.model_dump() for p in c.work_refs],
                blocked=self._blocked_marking(c.chunk_id),
            )
            for i, c in enumerate(ready)
        ]
        return QueuePeekResponse(entries=entries)

    def _blocked_marking(self, chunk_id: str) -> BlockedView | None:
        lever = self._levers.find(HubLever.DEPENDENCY_UNMET.value, chunk_id)
        if lever is None:
            return None
        return BlockedView(prerequisite_chunk_id=str(lever.payload.get("prerequisite_chunk_id", "unknown")))

    def peek_matched(
        self, *, runner_id: str | None, capabilities: Sequence[RunnerCapability], policy: str
    ) -> QueuePeekResponse:
        """The mock's own mirror of ``blizzard.hub.domain.operations.queue.select_matched_entry``, over its
        flat ``ChunkState`` graph (``mock_hub.domain.matching``): at most one entry, never
        blocked, policy applied to both the capability and blocked-dependency dimensions before
        selection. ``runner_id`` stands in for the real verb's authenticated principal; naming
        none raises :class:`UnresolvableRunner`, the mock's ``401`` in every mode."""
        if not runner_id or self._state.get_runner(runner_id) is None:
            raise UnresolvableRunner(runner_id or "")
        match_policy = matching.QueueMatchPolicy.of(policy)
        ready = [c for c in self._state.list_chunks() if not c.claimed and c.status is ChunkStatus.READY]
        for position, chunk in enumerate(ready):
            unusable = self._blocked_marking(chunk.chunk_id) is not None or matching.capability_ineligible(
                chunk, chunk.current_node_id or chunk.entry, capabilities
            )
            if not unusable:
                return QueuePeekResponse(
                    entries=[
                        QueuePeekEntry(
                            chunk_id=chunk.chunk_id,
                            graph_id=chunk.graph_id,
                            position=position,
                            work_refs=[p.model_dump() for p in chunk.work_refs],
                            blocked=None,
                        )
                    ]
                )
            if match_policy is matching.QueueMatchPolicy.HOLD:
                return QueuePeekResponse(entries=[])
        return QueuePeekResponse(entries=[])

    # -- claim -------------------------------------------------------------

    def claim(
        self, chunk_id: str, *, runner_id: str, workspace_id: str, environment_ids: list[str]
    ) -> RouteClaimResponse:
        chunk = self._require(chunk_id)
        # Re-read fresh, never cached from the peek: a capability change landing after this
        # runner's peek must not race the claim.
        registration = refuse_braked_runner(self._state.get_runner(runner_id), runner_id=runner_id)
        chunk.refuse_claim()
        blocked = self._levers.find(HubLever.DEPENDENCY_UNMET.value, chunk_id)
        if blocked is not None:
            self._levers.consume(blocked)
            raise DependencyUnmet(str(blocked.payload.get("prerequisite_chunk_id", "unknown")))
        # No capabilities at all is never revalidated.
        if registration.capabilities:
            node_id = chunk.current_node_id or chunk.entry
            if matching.capability_ineligible(chunk, node_id, registration.capabilities):
                raise ClaimIncompatible(runner_id)
        chunk.claimed = True
        chunk.route_runner_id = runner_id
        chunk.route_workspace_id = workspace_id
        chunk.route_environment_ids = list(environment_ids)
        chunk.current_node_id = chunk.entry
        chunk.status = ChunkStatus.RUNNING
        self._state.put_chunk(chunk)
        return RouteClaimResponse(
            chunk_id=chunk_id,
            runner_id=runner_id,
            workspace_id=workspace_id,
            environment_ids=list(environment_ids),
            envelope=self._envelope(chunk, chunk.entry, epoch=chunk.latest_epoch),
            # A per-claim capability token. Deterministic on
            # purpose, so a scenario can predict it.
            route_token=f"mock-route-token-{chunk_id}-{chunk.latest_epoch}",
        )

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        """Rotate the chunk's live route capability token —
        mirrors the real hub's ``POST /api/fleet/chunks/{id}/route-token``. Why it exists:
        `blizzard/src/blizzard/hub/domain/execution/claim.py`'s ``ClaimService.rekey``. Deterministic,
        like the claim's own token, but a counter folded in so a re-key never echoes it back."""
        chunk = self._require(chunk_id)
        if not chunk.claimed:
            raise ChunkNotFound(f"chunk {chunk_id} has no live route")
        chunk.refuse_rekey()
        chunk.route_token_rekey_count += 1
        self._state.put_chunk(chunk)
        return RouteTokenRekeyResponse(
            chunk_id=chunk_id,
            route_token=f"mock-route-token-{chunk_id}-{chunk.latest_epoch}-rekey{chunk.route_token_rekey_count}",
        )

    # -- reads -------------------------------------------------------------

    def chunk_detail(self, chunk_id: str) -> ChunkDetail:
        self._consult_chunk_unknown(chunk_id)
        chunk = self._require(chunk_id)
        route = None
        if chunk.claimed:
            runner_id = chunk.route_runner_id or ""
            conflict = self._levers.find(HubLever.CONFLICTING_FACT.value, chunk_id)
            if conflict is not None:
                self._levers.consume(conflict)
                runner_id = str(conflict.payload.get("runner_id", "other-runner"))
            route = RouteView(
                runner_id=runner_id,
                workspace_id=chunk.route_workspace_id or "",
                environment_ids=chunk.route_environment_ids,
            )
        escalation = None
        if chunk.escalation is not None:
            escalation = ChunkEscalationView(
                epoch=chunk.escalation.epoch,
                takeover_command=chunk.escalation.takeover_command,
                wrapped_takeover_command=chunk.escalation.wrapped_takeover_command,
                cause=chunk.escalation.cause,
                detail=chunk.escalation.detail,
            )
        questions = [self._question_view(q) for q in self._state.list_questions() if q.chunk_id == chunk_id]
        status = chunk.status
        return ChunkDetail(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            status=status.value,
            current_node_id=chunk.current_node_id,
            pausable=status in PAUSABLE_STATUSES,
            status_if_paused=status_if_paused(status).value,
            completable=status is not ChunkStatus.DONE,
            deletable=status in PRE_CLAIM_STATUSES,
            graph_editable=status in PRE_CLAIM_STATUSES and chunk.current_node_id is None,
            terminal=status in TERMINAL_STATUSES,
            current_node_terminal=chunk.current_node_id == TERMINAL,
            latest_epoch=chunk.latest_epoch or None,
            work_refs=[p.model_dump() for p in chunk.work_refs],
            default_model=list(chunk.default_model),
            default_effort=chunk.default_effort,
            default_harnesses=list(chunk.default_harnesses),
            route=route,
            escalation=escalation,
            questions=questions,
        )

    def chunk_statuses(self, chunk_ids: list[str]) -> list[ChunkStatusView]:
        """The runner tick's slim batch status read — mirrors the real hub's
        ``GET /api/fleet/chunk-statuses``. De-dupes ``chunk_ids`` preserving order; an id
        unknown to the store is silently omitted, never a 404. The ``chunk_unknown`` lever
        (see :meth:`_consult_chunk_unknown`) applies here too, but as an omission rather
        than its usual raise — this read never 404s, so a scripted id is simply left out,
        the same way a genuinely unseeded id already is. The ``conflicting_fact`` lever
        applies too, exactly as it does to ``chunk_detail``'s ``route.runner_id`` — the
        runner tick reads routes only through this endpoint now, so it must be able to
        drive the same detach/abandon path."""
        ids = list(dict.fromkeys(chunk_ids))
        views: list[ChunkStatusView] = []
        for chunk_id in ids:
            unknown = self._levers.find(HubLever.CHUNK_UNKNOWN.value, chunk_id)
            if unknown is not None:
                self._levers.consume(unknown)
                continue
            chunk = self._state.get_chunk(chunk_id)
            if chunk is None:
                continue
            route_runner_id = chunk.route_runner_id if chunk.claimed else None
            if chunk.claimed:
                conflict = self._levers.find(HubLever.CONFLICTING_FACT.value, chunk_id)
                if conflict is not None:
                    self._levers.consume(conflict)
                    route_runner_id = str(conflict.payload.get("runner_id", "other-runner"))
            views.append(
                ChunkStatusView(
                    chunk_id=chunk.chunk_id,
                    status=chunk.status.value,
                    route_runner_id=route_runner_id,
                    latest_epoch=chunk.latest_epoch or None,
                )
            )
        return views

    def work_items(self, chunk_id: str) -> WorkItemsView:
        """A chunk's pass-through work items — one canned entry per pointer.

        The mock carries no forge integration; this exists so the route is
        reachable at all, not for work-item-content behavior.
        """
        chunk = self._require(chunk_id)
        now = self._clock.now().isoformat()
        return WorkItemsView(
            items=[
                WorkItemEntry(
                    source=p.source,
                    ref=p.ref,
                    hub_source=p.source == RESERVED_HUB_SOURCE_NAME,
                    fetched_at=now,
                    title=f"mock item {p.source}#{p.ref}",
                )
                for p in chunk.work_refs
            ]
        )

    def garden_findings(self, chunk_id: str) -> list[FindingView]:
        """A chunk's own routine-and-scope-derived live finding bucket — mirrors
        ``GET /api/fleet/chunks/{id}/garden/findings``. Raises :class:`NoRunContext` for
        a chunk seeded with no ``garden_run`` — not a routine run — rather than
        answering an empty bucket."""
        chunk = self._garden_run_or_404(chunk_id)
        assert chunk.garden_run is not None  # narrowed by `_garden_run_or_404`
        run = chunk.garden_run
        return [
            _finding_view(f, routine_name=run.routine_name, scope_slug=run.scope_slug)
            for f in chunk.garden_findings
            if f.live
        ]

    def garden_proposals(
        self, chunk_id: str, *, state: RoutineProposalState = RoutineProposalState.OPEN
    ) -> list[GardenProposalView]:
        """A chunk's own routine's garden proposal bucket, filtered by ``state`` — mirrors
        ``GET /api/fleet/chunks/{id}/garden/proposals``. Raises :class:`NoRunContext` for
        a chunk seeded with no ``garden_run`` — not a routine run — rather than
        answering an empty bucket. A seeded proposal is open when it carries no
        ``closure``, closed when it does."""
        chunk = self._garden_run_or_404(chunk_id)
        assert chunk.garden_run is not None  # narrowed by `_garden_run_or_404`
        now = self._clock.now().isoformat()
        proposals = chunk.garden_proposals
        if state is RoutineProposalState.OPEN:
            proposals = [p for p in proposals if p.closure is None]
        elif state is RoutineProposalState.CLOSED:
            proposals = [p for p in proposals if p.closure is not None]
        return [_proposal_view(p, routine_name=chunk.garden_run.routine_name, now=now) for p in proposals]

    def _garden_run_or_404(self, chunk_id: str) -> ChunkState:
        """The chunk a worker's own routine-run-scoped read is confined to — shared by
        the garden reads and the analytics reads, all of which 404 a
        chunk seeded with no ``garden_run`` rather than answering an empty bucket."""
        chunk = self._require(chunk_id)
        if chunk.garden_run is None:
            raise NoRunContext(f"chunk {chunk_id} carries no run context — not a routine run")
        return chunk

    def analytics_counts_files(self, chunk_id: str) -> AnalyticsCountsResponse:
        """Mirrors ``GET /api/fleet/chunks/{id}/analytics/counts/files`` — the chunk's
        own seeded rows, served as-is (the mock does not aggregate)."""
        chunk = self._garden_run_or_404(chunk_id)
        return _counts_response(chunk.analytics.counts_files)

    def analytics_counts_skills(self, chunk_id: str) -> AnalyticsCountsResponse:
        chunk = self._garden_run_or_404(chunk_id)
        return _counts_response(chunk.analytics.counts_skills)

    def analytics_counts_agent_types(self, chunk_id: str) -> AnalyticsCountsResponse:
        chunk = self._garden_run_or_404(chunk_id)
        return _counts_response(chunk.analytics.counts_agent_types)

    def analytics_counts_nodes(self, chunk_id: str) -> AnalyticsCountsResponse:
        chunk = self._garden_run_or_404(chunk_id)
        return _counts_response(chunk.analytics.counts_nodes)

    def analytics_spend_nodes(self, chunk_id: str) -> AnalyticsSpendResponse:
        chunk = self._garden_run_or_404(chunk_id)
        return _spend_response(chunk.analytics.spend_nodes)

    def analytics_spend_graphs(self, chunk_id: str) -> AnalyticsSpendResponse:
        chunk = self._garden_run_or_404(chunk_id)
        return _spend_response(chunk.analytics.spend_graphs)

    def answered_findings(self, chunk_id: str) -> list[FindingView]:
        """The findings the chunk's own accepted, minted garden proposal answers —
        mirrors ``GET /api/fleet/chunks/{id}/findings``. Raises
        :class:`NoAnsweredProposal` for a chunk seeded with no
        ``garden_answered_findings`` — answering no such proposal — rather than
        answering an empty bucket."""
        chunk = self._require(chunk_id)
        if chunk.garden_answered_findings is None:
            raise NoAnsweredProposal(f"chunk {chunk_id} answers no accepted, minted garden proposal")
        return [
            _finding_view(f, routine_name=f.routine_name, scope_slug=f.scope_slug)
            for f in chunk.garden_answered_findings
        ]

    def answered_finding(self, chunk_id: str, *, finding_id: str) -> FindingView:
        """One finding within the chunk's own answered set — mirrors
        ``GET /api/fleet/chunks/{id}/findings/{finding_id}``. Raises
        :class:`NoAnsweredProposal` the same as :meth:`answered_findings`, or
        :class:`FindingNotInAnsweredSet` for an id outside it."""
        for view in self.answered_findings(chunk_id):
            if view.finding_id == finding_id:
                return view
        raise FindingNotInAnsweredSet(f"finding {finding_id} is not among the findings chunk {chunk_id} answers")

    def envelope(self, chunk_id: str) -> NodeEnvelope:
        """The current node's envelope (idempotent re-read, D-090).

        The ``stale_envelope`` lever stamps ``latest_epoch - 1`` so a completion built
        from it is fenced out as a zombie — the runner's stale-envelope path (D-007)."""
        self._consult_chunk_unknown(chunk_id)
        chunk = self._require(chunk_id)
        chunk.refuse_envelope()
        node_id = chunk.current_node_id or chunk.entry
        epoch = chunk.latest_epoch
        stale = self._levers.find(HubLever.STALE_ENVELOPE.value, chunk_id)
        if stale is not None:
            self._levers.consume(stale)
            epoch = max(chunk.latest_epoch - 1, 0)
        return self._envelope(chunk, node_id, epoch=epoch)

    # -- fact intake (fence + full vocabulary) ------------------------------

    def ingest_facts(self, runner_id: str, facts: list[dict[str, Any]]) -> RunnerFactAck:
        """Apply a batched ``POST /events`` push, partitioned into
        ``applied``/``already_applied``/``rejected`` against a per-runner
        high-water mark. A seq at or under the mark is re-acked without
        re-applying; an unrecognized ``kind`` is rejected, not silently applied.
        """
        mark = self._fact_high_water.get(runner_id, 0)
        applied: list[int] = []
        already_applied: list[int] = []
        rejected: list[int] = []
        for fact in sorted(facts, key=lambda f: int(f.get("seq", 0))):
            seq = int(fact.get("seq", 0))
            if seq <= mark:
                already_applied.append(seq)
                continue
            kind = str(fact.get("kind", ""))
            payload = fact.get("payload")
            if not isinstance(payload, dict):
                payload = {}
            if self._apply_fact(runner_id, kind, payload):
                applied.append(seq)
                mark = max(mark, seq)
            else:
                rejected.append(seq)
        self._fact_high_water[runner_id] = mark
        return RunnerFactAck(
            runner_id=runner_id, high_water=mark, applied=applied, already_applied=already_applied, rejected=rejected
        )

    # -- transcript intake (its own lane, its own high-water) ---------

    def ingest_transcripts(self, runner_id: str, records: list[dict[str, Any]]) -> TranscriptSegmentAck:
        """Apply a batched ``POST /transcripts`` push against the transcript lane's own high-water mark.
        Mirrors the real hub's two caps and natural-key short-circuit, retaining each accepted record by
        lease for :meth:`lease_transcript`; a capped record is never retained. A record whose epoch another
        runner owns is refused unstored, on replay too, and the mark still advances past it."""
        mark = self._transcript_high_water.get(runner_id, 0)
        applied: list[int] = []
        already_applied: list[int] = []
        capped: list[int] = []
        refused: list[int] = []
        for record in sorted(records, key=lambda r: int(r.get("seq", 0))):
            seq = int(record.get("seq", 0))
            key = (str(record.get("segment_id", "")), int(record.get("turn_range_start", 0)))
            owner = self._epoch_owners.get((str(record.get("chunk_id", "")), int(record.get("epoch", 0))))
            if not ships_from_lease_holder(owner, runner_id):
                refused.append(seq)
                mark = max(mark, seq)
                continue
            if seq <= mark:
                # A lost-ack replay of an already-decided seq still reports its own outcome.
                (capped if self._transcript_key_state.get(key) == "rejected" else already_applied).append(seq)
                continue
            mark = max(mark, seq)
            if self._transcript_key_state.get(key) == "accepted":
                # Mirrors `TranscriptIngestService._apply`'s early return: already accepted
                # under this key — no re-adjudication, re-crediting, or re-retention.
                applied.append(seq)
                continue
            chunk_id = str(record.get("chunk_id", ""))
            size = len(json.dumps(record.get("turns", [])).encode("utf-8"))
            stored = self._transcript_chunk_bytes.get(chunk_id, 0)
            if size > _TRANSCRIPT_RECORD_MAX_BYTES or stored + size > _TRANSCRIPT_CHUNK_BUDGET_MAX_BYTES:
                self._transcript_key_state[key] = "rejected"
                capped.append(seq)
                continue
            self._transcript_key_state[key] = "accepted"
            self._transcript_chunk_bytes[chunk_id] = stored + size
            lease_key = (chunk_id, str(record.get("node_id", "")), int(record.get("epoch", 0)))
            self._transcript_segments.setdefault(lease_key, {})[key] = dict(record)
            applied.append(seq)
        self._transcript_high_water[runner_id] = mark
        return TranscriptSegmentAck(
            runner_id=runner_id,
            high_water=mark,
            applied=applied,
            already_applied=already_applied,
            capped=capped,
            refused=refused,
        )

    def lease_transcript(self, chunk_id: str, *, node_id: str, epoch: int) -> LeaseTranscriptView:
        """The transcript lane's own read-back — one lease's retained turns across every
        spawn generation, ordered like the real store's ``records_for_lease``
        (``spawn_generation, segment_id, turn_range_start``) rather than by retention, which a
        flush or a re-offer can disorder. The mock has no caller-identity concept here at
        all — no principal, no per-runner filter — so it serves the lease to anyone."""
        records = self._transcript_segments.get((chunk_id, node_id, epoch), {})

        def _order_key(r: dict[str, Any]) -> tuple[int, str, int]:
            return int(r.get("spawn_generation", 0)), str(r.get("segment_id", "")), int(r.get("turn_range_start", 0))

        turns: list[dict[str, Any]] = []
        for record in sorted(records.values(), key=_order_key):
            turns.extend(record.get("turns", []))
        renumbered = [{**turn, "index": i} for i, turn in enumerate(turns)]
        return LeaseTranscriptView(chunk_id=chunk_id, node_id=node_id, epoch=epoch, turns=renumbered)

    def _apply_fact(self, runner_id: str, kind: str, payload: dict[str, Any]) -> bool:
        """Dispatch one fact by ``kind``; ``True`` = applied, ``False`` = rejected.

        A known kind naming an unknown chunk still counts applied (pinned by
        tests/test_mock_hub.py).
        """
        if kind == LEASE_MINTED:
            chunk = self._state.get_chunk(str(payload.get("chunk_id", "")))
            if chunk is not None:
                self._advance_fence(chunk, int(payload.get("epoch", 0)), runner_id=runner_id)
            return True
        if kind == ESCALATION_RECORDED:
            chunk = self._state.get_chunk(str(payload.get("chunk_id", "")))
            if chunk is not None:
                epoch = int(payload.get("epoch", 0))
                if self._fence_refusal(chunk, epoch) is not None:
                    return False
                self._record_escalation(
                    chunk,
                    epoch=epoch,
                    takeover_command=str(payload.get("takeover_command", "")),
                    wrapped_takeover_command=str(payload.get("wrapped_takeover_command", "")),
                    cause=_optional_text(payload.get("cause")),
                    detail=_optional_text(payload.get("detail")),
                )
            return True
        if kind == QUESTION_ASKED:
            question_id = str(payload.get("question_id", ""))
            chunk_id = str(payload.get("chunk_id", ""))
            if not question_id or not chunk_id:
                return False
            asked_on = self._state.get_chunk(chunk_id)
            if (
                asked_on is not None
                and self._state.get_question(question_id) is None
                and self._fence_refusal(asked_on, int(payload.get("epoch", 0))) is not None
            ):
                return False
            self._state.put_question(
                QuestionState(
                    question_id=question_id,
                    chunk_id=chunk_id,
                    node_id=payload.get("node_id"),
                    session_id=payload.get("session_id"),
                    harness_id=payload.get("harness_id"),
                    runner_id=runner_id,
                    epoch=int(payload.get("epoch", 0)),
                    question=str(payload.get("question", "")),
                    options=list(payload.get("options") or []),
                    asked_at=str(payload.get("asked_at", "")),
                )
            )
            return True
        if kind == ANSWER_DELIVERED:
            question = self._state.get_question(str(payload.get("question_id", "")))
            if question is None:
                return False
            if question.delivery_refusal(chunk_id=str(payload.get("chunk_id", ""))) is not None:
                return False
            if question.delivered:
                return True  # a repeat delivery is a replay that writes nothing
            question.delivered = True
            question.delivered_at = self._clock.now().isoformat()
            self._state.put_question(question)
            return True
        # The three runner-scoped kinds land on facts held per runner_id, not on the registry
        # row: the real hub accepts and *persists* each without a registration, so a report
        # that outruns its registration is still readable once that lands.
        if kind == RUNNER_LOCALLY_PAUSED:
            reported = self._state.reported_facts(runner_id)
            reported.locally_paused = True
            reported.locally_paused_by = str(payload.get("by", "operator"))
            reported.locally_paused_reason = payload.get("reason")
            return True
        if kind == RUNNER_LOCALLY_RESUMED:
            reported = self._state.reported_facts(runner_id)
            reported.locally_paused = False
            reported.locally_paused_by = None
            reported.locally_paused_reason = None
            return True
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED:
            # A non-empty string slug upserts per slug; a sibling's stored view is untouched.
            slug = payload.get("slug")
            if not isinstance(slug, str) or not slug:
                return False
            self._state.reported_facts(runner_id).subscription_usage[slug] = self._usage_view(payload, slug=slug)
            return True
        if kind == EXTERNAL_SUBSCRIPTION_USAGE_MISSED:
            # Sibling to the sampled kind above — upserts per slug into its own
            # dict, never touching the sample a sibling slug (or this same slug) holds.
            slug = payload.get("slug")
            if not isinstance(slug, str) or not slug:
                return False
            self._state.reported_facts(runner_id).subscription_usage_misses[slug] = self._usage_miss(payload, slug=slug)
            return True
        # usage.recorded / event.recorded are accepted as no-ops.
        return kind in (USAGE_RECORDED, EVENT_RECORDED)

    # -- completion apply --------------------------------------------------

    def apply_completion(self, chunk_id: str, *, epoch: int, from_node_id: str, choice: str) -> ApplyResponse:
        """Advance the chunk on a node-step completion — epoch-fenced and idempotent."""
        chunk = self._require(chunk_id)

        replay = self._levers.find(HubLever.REPLAY.value, chunk_id)
        if replay is not None and chunk.last_response is not None:
            self._levers.consume(replay)
            return chunk.last_response  # a duplicate delivery — the previous response, no re-advance

        key = f"{from_node_id}#{epoch}"
        if key in chunk.applied:
            return self._rebuild(chunk, chunk.applied[key])  # idempotent re-apply (D-090)

        if epoch < chunk.latest_epoch:
            return self._fail(chunk, f"stale epoch {epoch} < {chunk.latest_epoch}")
        incoherent = self._incoherent_attempt(chunk, from_node_id=from_node_id, epoch=epoch)
        if incoherent is not None:
            return self._fail(chunk, incoherent)

        node = chunk.node(from_node_id)
        if node is None:
            return self._fail(chunk, f"unknown node {from_node_id!r}")
        hub_executed = chunk.hub_executed(from_node_id)
        if hub_executed is not None:
            return self._fail(chunk, hub_executed)
        target = self._resolve_choice(node, choice)
        if target is None:
            return self._fail(chunk, f"unknown choice {choice!r} at {from_node_id!r}")

        response = self._advance(chunk, target)
        chunk.applied[key] = response.outcome
        chunk.last_response = response
        self._state.put_chunk(chunk)
        return response

    def apply_decision(self, chunk_id: str, *, epoch: int, from_node_id: str) -> ApplyResponse:
        """A runner-config gate: park the chunk (``parked_at_gate``) — the human loop (D-032)."""
        chunk = self._require(chunk_id)
        if epoch < chunk.latest_epoch:
            return self._fail(chunk, f"stale epoch {epoch} < {chunk.latest_epoch}")
        incoherent = self._incoherent_attempt(chunk, from_node_id=from_node_id, epoch=epoch)
        if incoherent is not None:
            return self._fail(chunk, incoherent)
        chunk.status = ChunkStatus.NEEDS_HUMAN
        self._state.put_chunk(chunk)
        return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail="parked at gate")

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        """``POST /chunks/{id}/hub-advance`` — drive a chunk parked at a hub-executor
        node one step (#65/#66). A chunk not parked at a hub-executor node is a
        no-op, ``ran=False`` (pinned by tests/test_mock_hub.py).
        """
        chunk = self._require(chunk_id)
        node = chunk.node(chunk.current_node_id) if chunk.current_node_id is not None else None
        parked = node is not None and node.executor is Executor.HUB and chunk.status is not ChunkStatus.DONE
        if not parked:
            return HubAdvanceResponse(
                chunk_id=chunk_id, status=chunk.status.value, ran=False, detail="not parked at a hub command node"
            )
        chunk.status = ChunkStatus.DONE
        self._state.put_chunk(chunk)
        return HubAdvanceResponse(
            chunk_id=chunk_id, status=chunk.status.value, ran=True, detail="hub node advanced to done"
        )

    # -- questions (ask/answer rendezvous) ----------------------------------

    def question_view(self, question_id: str) -> QuestionView:
        question = self._state.get_question(question_id)
        if question is None:
            raise QuestionNotFound(f"unknown question {question_id}")
        return self._question_view(question)

    def answer_question(self, question_id: str, *, answer: str, answered_by: str = "operator") -> None:
        """Test-control only (``POST /_seed/answer``) — plays the operator's own
        ``POST /questions/{id}/answer`` (board-only on the real hub, out of scope for
        the fleet mirror) so a scenario can make the runner's poll return
        ``answered=True`` without a real operator surface."""
        question = self._state.get_question(question_id)
        if question is None:
            raise QuestionNotFound(f"unknown question {question_id}")
        question.answered = True
        question.answer = answer
        question.answered_by = answered_by
        question.answered_at = self._clock.now().isoformat()
        self._state.put_question(question)

    def stop_chunk(self, chunk_id: str) -> None:
        """Test-control only (``POST /_seed/stop``) — plays the operator's stop verb, which
        the fleet mirror carries no route for. One write: the status goes terminal and the
        live route releases in the same step, mirroring the real hub's stop so
        ``chunk_detail`` never serves a route alongside a stopped status. The chunk's seeded
        graph and escalation state are untouched."""
        chunk = self._require(chunk_id)
        chunk.status = ChunkStatus.STOPPED
        chunk.claimed = False
        self._state.put_chunk(chunk)

    # -- registry ----------------------------------------------------------

    def register(
        self,
        runner_id: str,
        *,
        workspace_id: str,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        env_capacity: int | None = None,
        capabilities: tuple[RunnerCapability, ...] = (),
        subscriptions: tuple[DeclaredSubscription, ...] | None = None,
        gates: tuple[str, ...] = (),
    ) -> bool:
        return self._state.upsert_runner(
            runner_id,
            workspace_id=workspace_id,
            at=self._clock.now(),
            url=url,
            redirect_uris=redirect_uris,
            env_capacity=env_capacity,
            capabilities=capabilities,
            declared_subscriptions=subscriptions,
            gates=gates,
        )

    def runner_view(self, runner_id: str) -> RunnerView | None:
        row = self._state.get_runner(runner_id)
        if row is None:
            return None
        # Reported facts are merged in at the read, so one that arrived before this
        # registration surfaces the moment the registration lands.
        reported = self._state.reported_facts(runner_id)
        return RunnerView(
            runner_id=row.runner_id,
            workspace_id=row.workspace_id,
            registered_at=row.registered_at.isoformat(),
            last_seen_at=row.last_seen_at.isoformat(),
            online=True,
            hub_paused=row.paused,
            locally_paused=reported.locally_paused,
            locally_paused_by=reported.locally_paused_by,
            locally_paused_reason=reported.locally_paused_reason,
            env_capacity=row.env_capacity,
            subscriptions=self._usage_condition_views(reported, declared_subscriptions=row.declared_subscriptions),
            capabilities=[
                RunnerCapabilityView(
                    harness_id=c.harness_id,
                    version=c.version,
                    tiers=list(c.tiers),
                    default=c.default,
                    available=c.available,
                )
                for c in row.capabilities
            ],
            gates=list(row.gates),
        )

    def _parse_instant(self, value: Any) -> datetime:
        """An ISO-8601 stamp off a fact payload, falling back to the clock's own instant on
        a missing or malformed one — shared by the sampled and missed fact handlers."""
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
                return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
            except ValueError:
                pass
        return self._clock.now()

    def _usage_view(self, payload: dict[str, Any], *, slug: str) -> SubscriptionUsageView:
        """One sampled payload as the mirrored view, with malformed windows omitted.

        ``sampled_at`` defaults exactly as the real hub's ingest does. ``name`` defaults to ``slug``
        itself for a payload with no additive ``name`` field, mirroring the real hub's own default."""
        windows = []
        entries = payload.get("windows")
        for entry in entries if isinstance(entries, list) else []:
            try:
                window = _ExternalSubscriptionUsageWindowFact.model_validate(entry)
                windows.append(
                    ExternalSubscriptionUsageWindowView(
                        window=window.window,
                        utilization_pct=window.utilization_pct,
                        resets_at=(
                            window.resets_at.isoformat()
                            if window.resets_at.tzinfo is not None
                            else window.resets_at.replace(tzinfo=UTC).isoformat()
                        ),
                        window_seconds=window.window_seconds,
                    )
                )
            except ValidationError:
                continue
        sampled_at = self._parse_instant(payload.get("sampled_at"))
        name = payload.get("name")
        return SubscriptionUsageView(
            slug=slug,
            name=str(name) if name else slug,
            sampled_at=sampled_at.isoformat(),
            windows=windows,
        )

    def _usage_miss(self, payload: dict[str, Any], *, slug: str) -> SubscriptionUsageMiss:
        """One missed-fact payload as the mirrored miss record — ``name``
        defaults to ``slug`` itself, mirroring :meth:`_usage_view`'s own default."""
        name = payload.get("name")
        reason = payload.get("reason")
        return SubscriptionUsageMiss(
            slug=slug,
            name=str(name) if name else slug,
            missed_at=self._parse_instant(payload.get("missed_at")),
            reason=str(reason) if reason else "",
        )

    @staticmethod
    def _usage_condition_views(
        reported: ReportedRunnerFacts, *, declared_subscriptions: tuple[DeclaredSubscription, ...] | None
    ) -> list[SubscriptionUsageView]:
        """Every subscription's rendered view. With a roster declared, one view per
        declared slug, whatever the age of its sample — the roster, not reporting, now
        decides membership; a slug reported but no longer declared is simply absent. With
        no roster declared, this is the reported-slug union it has always been. No
        staleness gate on either path: this mock never ages a report out on its own."""
        if declared_subscriptions is not None:
            return MockHubService._roster_views(declared_subscriptions, reported)
        return MockHubService._reported_views(reported)

    @staticmethod
    def _roster_views(
        roster: tuple[DeclaredSubscription, ...], reported: ReportedRunnerFacts
    ) -> list[SubscriptionUsageView]:
        """The roster-gated membership rule — one view per declared slug; a duplicate
        declared slug collapses, first wins."""
        declared: dict[str, DeclaredSubscription] = {}
        for declaration in roster:
            declared.setdefault(declaration.slug, declaration)
        views: list[SubscriptionUsageView] = []
        for slug in sorted(declared):
            declaration = declared[slug]
            sample = reported.subscription_usage.get(slug)
            miss = reported.subscription_usage_misses.get(slug)
            views.append(
                SubscriptionUsageView(
                    slug=slug,
                    name=declaration.name,
                    sampled_at=sample.sampled_at if sample is not None else None,
                    windows=sample.windows if sample is not None else [],
                    condition=_CREDENTIAL_LAPSED_CONDITION if MockHubService._lapsed(sample, miss) else None,
                    miss_reason=miss.reason if miss is not None else None,
                    missed_at=miss.missed_at.isoformat() if miss is not None else None,
                )
            )
        return views

    @staticmethod
    def _reported_views(reported: ReportedRunnerFacts) -> list[SubscriptionUsageView]:
        """The rosterless fallback — unioned across samples and misses by slug: a sample or
        a lapsed miss admits the slug; once admitted, a surviving sample's fields are kept
        regardless of a lapsed condition."""
        slugs = sorted(set(reported.subscription_usage) | set(reported.subscription_usage_misses))
        views: list[SubscriptionUsageView] = []
        for slug in slugs:
            sample = reported.subscription_usage.get(slug)
            miss = reported.subscription_usage_misses.get(slug)
            lapsed = MockHubService._lapsed(sample, miss)
            if sample is not None:
                views.append(
                    sample.model_copy(
                        update={
                            "condition": _CREDENTIAL_LAPSED_CONDITION if lapsed else None,
                            "miss_reason": miss.reason if miss is not None else None,
                            "missed_at": miss.missed_at.isoformat() if miss is not None else None,
                        }
                    )
                )
            elif lapsed:
                assert miss is not None  # narrowed by `_lapsed`'s own condition
                views.append(
                    SubscriptionUsageView(
                        slug=slug,
                        name=miss.name,
                        sampled_at=None,
                        windows=[],
                        condition=_CREDENTIAL_LAPSED_CONDITION,
                        miss_reason=miss.reason,
                        missed_at=miss.missed_at.isoformat(),
                    )
                )
        return views

    @staticmethod
    def _lapsed(sample: SubscriptionUsageView | None, miss: SubscriptionUsageMiss | None) -> bool:
        """``True`` iff this slug's newest miss is a ``credential_lapsed`` newer than its
        newest (or absent) sample. No staleness gate on either path: this mock never ages a
        report out on its own."""
        if miss is None or miss.reason != _CREDENTIAL_LAPSED_CONDITION:
            return False
        return sample is None or sample.sampled_at is None or miss.missed_at > datetime.fromisoformat(sample.sampled_at)

    def set_paused(self, runner_id: str, paused: bool) -> None:
        row = self._state.get_runner(runner_id)
        if row is not None:
            row.paused = paused

    def pop_drop_ack(self, chunk_id: str) -> bool:
        """True (consuming the lever) if ``drop_ack`` is armed for the chunk.

        The completions route calls this *after* the apply has advanced the real state,
        to decide whether to drop the ack (answer 503) — the transition landed, so the
        runner's re-flush is idempotent (D-090)."""
        lever = self._levers.find(HubLever.DROP_ACK.value, chunk_id)
        if lever is None:
            return False
        self._levers.consume(lever)
        return True

    # -- internals ---------------------------------------------------------

    def _incoherent_attempt(self, chunk: ChunkState, *, from_node_id: str, epoch: int) -> str | None:
        """:meth:`ChunkState.incoherent_attempt`, fed the attempt's own unanswered questions."""
        open_question_ids = [
            q.question_id
            for q in self._state.list_questions()
            if q.chunk_id == chunk.chunk_id and q.epoch == epoch and not q.answered
        ]
        return chunk.incoherent_attempt(from_node_id=from_node_id, epoch=epoch, open_question_ids=open_question_ids)

    def _advance_fence(self, chunk: ChunkState, epoch: int, *, runner_id: str) -> None:
        """The ``lease.minted`` fence advance (D-044). The first runner to mint an epoch owns
        it, which decides whose transcript records that epoch admits."""
        chunk.latest_epoch = max(chunk.latest_epoch, epoch)
        self._epoch_owners.setdefault((chunk.chunk_id, epoch), runner_id)
        self._state.put_chunk(chunk)

    @staticmethod
    def _fence_refusal(chunk: ChunkState, epoch: int) -> str | None:
        if chunk.ended:
            return "chunk is terminal"
        if chunk.latest_epoch and epoch < chunk.latest_epoch:
            return f"stale epoch {epoch}; chunk is at {chunk.latest_epoch}"
        return None

    def _record_escalation(
        self,
        chunk: ChunkState,
        *,
        epoch: int,
        takeover_command: str,
        wrapped_takeover_command: str = "",
        cause: str | None = None,
        detail: str | None = None,
    ) -> None:
        """The ``escalation.recorded`` write."""
        chunk.escalation = EscalationState(
            epoch=epoch,
            takeover_command=takeover_command,
            wrapped_takeover_command=wrapped_takeover_command,
            cause=cause,
            detail=detail,
        )
        self._state.put_chunk(chunk)

    def _consult_chunk_unknown(self, chunk_id: str) -> None:
        """The ``chunk_unknown`` lever: a chunk-scoped read reports a genuine 404
        without deleting the chunk's actual seeded state — the runner's env-release
        trigger (commit ``68238d0``)."""
        unknown = self._levers.find(HubLever.CHUNK_UNKNOWN.value, chunk_id)
        if unknown is not None:
            self._levers.consume(unknown)
            raise ChunkNotFound(f"unknown chunk {chunk_id}")

    def _question_view(self, question: QuestionState) -> QuestionView:
        return QuestionView(
            question_id=question.question_id,
            chunk_id=question.chunk_id,
            node_id=question.node_id,
            session_id=question.session_id,
            harness_id=question.harness_id,
            runner_id=question.runner_id,
            epoch=question.epoch,
            question=question.question,
            options=list(question.options),
            asked_at=question.asked_at,
            answered=question.answered,
            answer=question.answer,
            answered_by=question.answered_by,
            answered_at=question.answered_at,
            delivered=question.delivered,
            delivered_at=question.delivered_at,
        )

    def _advance(self, chunk: ChunkState, target: str) -> ApplyResponse:
        if target == TERMINAL:
            chunk.status = ChunkStatus.DONE
            chunk.current_node_id = TERMINAL
            return ApplyResponse(outcome=ApplyOutcome.DONE, detail="reached terminal")
        node = chunk.node(target)
        if node is None:
            return self._fail(chunk, f"choice points at unknown node {target!r}")
        chunk.current_node_id = target
        if node.executor is Executor.HUB:
            chunk.status = ChunkStatus.DONE
            return ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN, detail="hub node took over")
        chunk.status = ChunkStatus.RUNNING
        return ApplyResponse(
            outcome=ApplyOutcome.NEXT,
            next_envelope=self._envelope(chunk, target, epoch=chunk.latest_epoch),
        )

    def _rebuild(self, chunk: ChunkState, outcome: ApplyOutcome) -> ApplyResponse:
        if outcome is ApplyOutcome.NEXT and chunk.current_node_id is not None:
            return ApplyResponse(
                outcome=outcome,
                next_envelope=self._envelope(chunk, chunk.current_node_id, epoch=chunk.latest_epoch),
            )
        return ApplyResponse(outcome=outcome, detail="idempotent re-apply")

    @staticmethod
    def _resolve_choice(node: NodeSpec, choice: str) -> str | None:
        for ch in node.choices:
            if ch.name == choice:
                return ch.to
        return None

    def _fail(self, chunk: ChunkState, detail: str) -> ApplyResponse:
        return ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail)

    def _envelope(self, chunk: ChunkState, node_id: str, *, epoch: int) -> NodeEnvelope:
        node = chunk.node(node_id)
        if node is None:
            raise ChunkNotFound(f"node {node_id!r} missing from chunk {chunk.chunk_id}")
        return NodeEnvelope(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            graph_name=chunk.graph_name,
            epoch=epoch,
            node=NodeConfig(
                node_id=node_id,
                node_name=node_id,
                executor=node.executor,
                session=node.session,
                session_source=node.session_source,
                session_name=node.session_name,
                session_model=list(node.session_model),
                session_effort=node.session_effort,
                session_harnesses=list(node.session_harnesses),
                session_compaction_window=node.session_compaction_window,
                session_rotate=RotatePolicyView(**node.session_rotate.model_dump())
                if node.session_rotate is not None
                else None,
                judged_by=node.judged_by,
                checks=node.checks,
                checks_cwd=node.checks_cwd,
                checks_timeout=node.checks_timeout,
                produces=node.produces,
                retries_max=node.retries_max,
                choices=[
                    EnvelopeChoice(name=c.name, description=c.description, requires_checks=c.requires_checks)
                    for c in node.choices
                ],
            ),
            prompt=node.prompt,
            judgement_prompt=node.judgement_prompt,
            work_refs=[{**p.model_dump(), "label": _work_ref_label(p)} for p in chunk.work_refs],
            graph_artifacts=[GraphArtifact(name=a.name, kind=a.kind, content=a.content) for a in chunk.graph_artifacts],
        )

    def _require(self, chunk_id: str) -> ChunkState:
        chunk = self._state.get_chunk(chunk_id)
        if chunk is None:
            raise ChunkNotFound(f"unknown chunk {chunk_id}")
        return chunk
