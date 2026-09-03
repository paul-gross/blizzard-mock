"""``MockHubService`` — the mock hub's business rules over its state and levers.

The domain layer (``bzh:domain-core``): advances a seeded chunk through its
scripted graph exactly as the real hub would. Levers shaping a response body
are consulted here; transport-edge levers live in the middleware.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import ValidationError

from blizzard_mock.clock import Clock
from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_hub.domain.levers import HubLever
from blizzard_mock.mock_hub.domain.models import (
    TERMINAL,
    ApplyOutcome,
    ChunkSpec,
    ChunkState,
    ChunkStatus,
    EscalationState,
    Executor,
    NodeSpec,
    QuestionState,
    SystemArtifactSpec,
)
from blizzard_mock.mock_hub.domain.state import IHubState
from blizzard_mock.mock_hub.domain.wire import (
    ApplyResponse,
    ChunkDetail,
    ChunkEscalationView,
    EnvelopeChoice,
    ExternalSubscriptionUsageView,
    ExternalSubscriptionUsageWindowView,
    FindingView,
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
    RunnerFactAck,
    RunnerView,
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

#: The real hub's caps, restated not imported (no ``blizzard`` dep); the daily-rate one needs
#: a wall clock. Keep the record cap >= the RUNNER's, or this rejects what the real hub stores.
_TRANSCRIPT_RECORD_MAX_BYTES = 10 * 1024 * 1024
_TRANSCRIPT_CHUNK_BUDGET_MAX_BYTES = 64 * 1024 * 1024


class ChunkNotFound(Exception):
    """No seeded chunk with that id."""


class QuestionNotFound(Exception):
    """No question with that id."""


class NoRunContext(Exception):
    """The chunk carries no seeded garden run identity — not a routine run."""


class SystemArtifactNotFound(Exception):
    """No published system artifact with that name."""


class ClaimConflict(Exception):
    """The chunk is already claimed — the losing runner gets a 409."""

    def __init__(self, held_by_runner_id: str) -> None:
        super().__init__(f"chunk already claimed by {held_by_runner_id}")
        self.held_by_runner_id = held_by_runner_id


class MockHubService:
    """The composition-root-wired service every mock-hub route delegates to."""

    def __init__(self, state: IHubState, levers: ILeverStore, clock: Clock) -> None:
        self._state = state
        self._levers = levers
        self._clock = clock
        #: Per-runner fact high-water mark — a seq at/under this mark is
        #: re-acked as ``already_applied`` rather than re-applied.
        self._fact_high_water: dict[str, int] = {}
        #: The transcript lane's own high-water mark (blizzard#247, D7) — a separate
        #: per-runner sequence from the fact lane's above.
        self._transcript_high_water: dict[str, int] = {}
        #: Accepted bytes per chunk — the chunk-budget cap's running total.
        self._transcript_chunk_bytes: dict[str, int] = {}
        #: The real hub's natural key (D8): a key's own accept/reject decision, independent of
        #: seq, mirroring `IWriteTranscriptSegments.natural_key_state`. Absent reads as "absent".
        self._transcript_key_state: dict[tuple[str, int], str] = {}
        #: Retained transcript records, keyed by lease (D2), then by each record's own
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
            default_model=list(spec.default_model),
            default_effort=spec.default_effort,
            entry=spec.entry,
            nodes=spec.nodes,
            work_refs=spec.work_refs,
            graph_artifacts=spec.graph_artifacts,
            garden_run=spec.garden_run,
            garden_findings=spec.garden_findings,
        )
        self._state.put_chunk(chunk)
        return chunk

    def reset(self) -> None:
        self._state.clear()
        self._levers.clear_all()
        self._fact_high_water.clear()
        self._transcript_high_water.clear()
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

    # -- queue -------------------------------------------------------------

    def peek(self) -> QueuePeekResponse:
        """Ready = seeded, unclaimed, not terminal — FIFO by insertion (D-080)."""
        ready = [c for c in self._state.list_chunks() if not c.claimed and c.status is ChunkStatus.READY]
        entries = [
            QueuePeekEntry(
                chunk_id=c.chunk_id,
                graph_id=c.graph_id,
                position=i,
                work_refs=[p.model_dump() for p in c.work_refs],
            )
            for i, c in enumerate(ready)
        ]
        return QueuePeekResponse(entries=entries)

    # -- claim -------------------------------------------------------------

    def claim(
        self, chunk_id: str, *, runner_id: str, workspace_id: str, environment_ids: list[str]
    ) -> RouteClaimResponse:
        chunk = self._require(chunk_id)
        if chunk.claimed:
            raise ClaimConflict(chunk.route_runner_id or "unknown")
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
            # A per-claim capability token (issue #84a). Deterministic on
            # purpose, so a scenario can predict it.
            route_token=f"mock-route-token-{chunk_id}-{chunk.latest_epoch}",
        )

    def rekey_route_token(self, chunk_id: str) -> RouteTokenRekeyResponse:
        """Rotate the chunk's live route capability token (issue paul-gross/blizzard#84b) —
        mirrors the real hub's ``POST /api/fleet/chunks/{id}/route-token``. Why it exists:
        `blizzard/src/blizzard/hub/domain/claim.py`'s ``ClaimService.rekey``. Deterministic,
        like the claim's own token, but a counter folded in so a re-key never echoes it back."""
        chunk = self._require(chunk_id)
        if not chunk.claimed:
            raise ChunkNotFound(f"chunk {chunk_id} has no live route")
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
            )
        questions = [self._question_view(q) for q in self._state.list_questions() if q.chunk_id == chunk_id]
        return ChunkDetail(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            status=chunk.status.value,
            current_node_id=chunk.current_node_id,
            latest_epoch=chunk.latest_epoch or None,
            work_refs=[p.model_dump() for p in chunk.work_refs],
            default_model=list(chunk.default_model),
            default_effort=chunk.default_effort,
            route=route,
            escalation=escalation,
            questions=questions,
        )

    def work_items(self, chunk_id: str) -> WorkItemsView:
        """A chunk's pass-through work items — one canned entry per pointer.

        The mock carries no forge integration; this exists so the route is
        reachable at all, not for work-item-content behavior.
        """
        chunk = self._require(chunk_id)
        now = self._clock.now().isoformat()
        return WorkItemsView(
            items=[
                WorkItemEntry(source=p.source, ref=p.ref, fetched_at=now, title=f"mock item {p.source}#{p.ref}")
                for p in chunk.work_refs
            ]
        )

    def garden_findings(self, chunk_id: str) -> list[FindingView]:
        """A chunk's own routine-and-scope-derived live finding bucket — mirrors
        ``GET /api/fleet/chunks/{id}/garden/findings``. Raises :class:`NoRunContext` for
        a chunk seeded with no ``garden_run`` — not a routine run — rather than
        answering an empty bucket."""
        chunk = self._require(chunk_id)
        if chunk.garden_run is None:
            raise NoRunContext(f"chunk {chunk_id} carries no run context — not a routine run")
        run = chunk.garden_run
        # `class_`'s alias is the Python keyword `class` — constructed by alias via
        # `model_validate`, the real hub's own `finding_view` shape.
        return [
            FindingView.model_validate(
                {
                    "finding_id": f.finding_id,
                    "routine_name": run.routine_name,
                    "scope_slug": run.scope_slug,
                    "class": f.class_,
                    "locus": f.locus,
                    "summary": f.summary,
                    "introduced": f.introduced,
                    "live": f.live,
                    "state": f.state,
                    "note": f.note,
                    "last_seen_at": f.last_seen_at,
                    "observed_count": f.observed_count,
                }
            )
            for f in chunk.garden_findings
            if f.live
        ]

    def envelope(self, chunk_id: str) -> NodeEnvelope:
        """The current node's envelope (idempotent re-read, D-090).

        The ``stale_envelope`` lever stamps ``latest_epoch - 1`` so a completion built
        from it is fenced out as a zombie — the runner's stale-envelope path (D-007)."""
        self._consult_chunk_unknown(chunk_id)
        chunk = self._require(chunk_id)
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

    # -- transcript intake (its own lane, its own high-water — D3) ---------

    def ingest_transcripts(self, runner_id: str, records: list[dict[str, Any]]) -> TranscriptSegmentAck:
        """Apply a batched ``POST /transcripts`` push against the transcript lane's own
        high-water mark (blizzard#247, D7). Mirrors the real hub's two caps and its
        natural-key short-circuit (D8), the state keyed by natural key rather than seq, and
        retains each accepted record by lease (blizzard#249, D2) for
        :meth:`lease_transcript` — a capped record is never retained, so it never reads back."""
        mark = self._transcript_high_water.get(runner_id, 0)
        applied: list[int] = []
        already_applied: list[int] = []
        capped: list[int] = []
        for record in sorted(records, key=lambda r: int(r.get("seq", 0))):
            seq = int(record.get("seq", 0))
            key = (str(record.get("segment_id", "")), int(record.get("turn_range_start", 0)))
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
            runner_id=runner_id, high_water=mark, applied=applied, already_applied=already_applied, capped=capped
        )

    def lease_transcript(self, chunk_id: str, *, node_id: str, epoch: int) -> LeaseTranscriptView:
        """The transcript lane's own read-back (D2) — one lease's retained turns across every
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
                self._advance_fence(chunk, int(payload.get("epoch", 0)))
            return True
        if kind == ESCALATION_RECORDED:
            chunk = self._state.get_chunk(str(payload.get("chunk_id", "")))
            if chunk is not None:
                self._record_escalation(
                    chunk,
                    epoch=int(payload.get("epoch", 0)),
                    takeover_command=str(payload.get("takeover_command", "")),
                    wrapped_takeover_command=str(payload.get("wrapped_takeover_command", "")),
                )
            return True
        if kind == QUESTION_ASKED:
            question_id = str(payload.get("question_id", ""))
            chunk_id = str(payload.get("chunk_id", ""))
            if not question_id or not chunk_id:
                return False
            self._state.put_question(
                QuestionState(
                    question_id=question_id,
                    chunk_id=chunk_id,
                    node_id=payload.get("node_id"),
                    session_id=payload.get("session_id"),
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
            # `answered` is set here too as a mock shortcut, so a scenario
            # that skips `POST /_seed/answer` still gets a coherent poll row.
            question.answered = True
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
            # Coerced here, never at the read: an accepted fact must not be able to make a
            # later `GET /runners/{id}` raise. Defaults mirror the real hub's own ingest.
            self._state.reported_facts(runner_id).external_subscription_usage = self._usage_view(payload)
            return True
        # usage.recorded / event.recorded (issue #125) are accepted as no-ops.
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
        if chunk.current_node_id is not None and from_node_id != chunk.current_node_id:
            return self._fail(chunk, f"unexpected from_node {from_node_id!r} (at {chunk.current_node_id!r})")

        node = chunk.node(from_node_id)
        if node is None:
            return self._fail(chunk, f"unknown node {from_node_id!r}")
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
        chunk.status = ChunkStatus.NEEDS_HUMAN
        self._state.put_chunk(chunk)
        return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail="parked at gate")

    # -- direct fact routes (non-buffered counterparts of /events) ----------

    def report_lease(self, chunk_id: str, *, epoch: int, runner_id: str) -> dict[str, Any]:
        """``POST /chunks/{id}/leases`` — the direct, non-buffered ``lease.minted``
        report; advances the fence exactly as the batched ``/events`` path, but
        404s on an unknown chunk rather than no-op'ing.
        """
        chunk = self._require(chunk_id)
        self._advance_fence(chunk, epoch)
        return {"chunk_id": chunk_id}

    def report_escalation(
        self,
        chunk_id: str,
        *,
        epoch: int,
        runner_id: str,
        takeover_command: str,
        wrapped_takeover_command: str = "",
    ) -> dict[str, Any]:
        """``POST /chunks/{id}/escalations`` — the direct, non-buffered
        ``escalation.recorded`` report; records the escalation exactly as the batched
        ``/events`` path does (the shared ``_record_escalation`` helper). Mirrors the
        real hub's 202 ``{"chunk_id"}`` body — no ``epoch`` in the response."""
        chunk = self._require(chunk_id)
        self._record_escalation(
            chunk, epoch=epoch, takeover_command=takeover_command, wrapped_takeover_command=wrapped_takeover_command
        )
        return {"chunk_id": chunk_id}

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
        live route releases in the same step, mirroring the real hub's stop (D2) so
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
    ) -> bool:
        return self._state.upsert_runner(
            runner_id,
            workspace_id=workspace_id,
            at=self._clock.now(),
            url=url,
            redirect_uris=redirect_uris,
            env_capacity=env_capacity,
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
            external_subscription_usage=reported.external_subscription_usage,
        )

    def _usage_view(self, payload: dict[str, Any]) -> ExternalSubscriptionUsageView:
        """One sampled payload as the mirrored view, total over any payload shape.

        ``sampled_at`` and ``windows`` default exactly as the real hub's ingest defaults them;
        a window that does not validate is dropped rather than failing the whole fact."""
        windows = []
        for entry in payload.get("windows") or []:
            try:
                windows.append(ExternalSubscriptionUsageWindowView.model_validate(entry))
            except ValidationError:
                continue
        sampled_at = payload.get("sampled_at")
        return ExternalSubscriptionUsageView(
            sampled_at=str(sampled_at) if sampled_at else self._clock.now().isoformat(), windows=windows
        )

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

    def _advance_fence(self, chunk: ChunkState, epoch: int) -> None:
        """The ``lease.minted`` fence advance (D-044), shared by the batched ``/events``
        dispatch and the direct ``POST /chunks/{id}/leases`` route."""
        chunk.latest_epoch = max(chunk.latest_epoch, epoch)
        self._state.put_chunk(chunk)

    def _record_escalation(
        self, chunk: ChunkState, *, epoch: int, takeover_command: str, wrapped_takeover_command: str = ""
    ) -> None:
        """The ``escalation.recorded`` write, shared by the batched ``/events`` dispatch
        and the direct ``POST /chunks/{id}/escalations`` route."""
        chunk.escalation = EscalationState(
            epoch=epoch, takeover_command=takeover_command, wrapped_takeover_command=wrapped_takeover_command
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
                mode=node.mode,
                choices=[
                    EnvelopeChoice(name=c.name, description=c.description, requires_checks=c.requires_checks)
                    for c in node.choices
                ],
            ),
            prompt=node.prompt,
            judgement_prompt=node.judgement_prompt,
            work_refs=[p.model_dump() for p in chunk.work_refs],
            graph_artifacts=[GraphArtifact(name=a.name, kind=a.kind, content=a.content) for a in chunk.graph_artifacts],
        )

    def _require(self, chunk_id: str) -> ChunkState:
        chunk = self._state.get_chunk(chunk_id)
        if chunk is None:
            raise ChunkNotFound(f"unknown chunk {chunk_id}")
        return chunk
