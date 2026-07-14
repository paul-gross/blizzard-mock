"""``MockHubService`` — the mock hub's business rules over its state and levers.

The domain layer (``bzh:domain-core``): it advances a seeded chunk through its scripted
graph exactly as the real hub would over the wire — claim mints a route and hands back
the first envelope, a completion is epoch-fenced and idempotent (D-007/D-090), a hub
(deliver) node "takes over" and the chunk derives ``done``. Levers that shape a *response
body* (``replay``, ``stale_envelope``, ``conflicting_fact``) are consulted here; the
transport-edge levers (``unreachable``, ``delay``) live in the middleware, and ``drop_ack``
is applied by the completions router after this service has advanced the real state.

All collaborators are injected at the composition root (``bzh:dependency-injection``).
"""

from __future__ import annotations

import uuid
from typing import Any

from blizzard_mock.clock import Clock
from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_hub.domain.levers import HubLever
from blizzard_mock.mock_hub.domain.models import (
    TERMINAL,
    ApplyOutcome,
    ChunkSpec,
    ChunkState,
    ChunkStatus,
    Executor,
    NodeSpec,
)
from blizzard_mock.mock_hub.domain.state import IHubState
from blizzard_mock.mock_hub.domain.wire import (
    ApplyResponse,
    ChunkDetail,
    EnvelopeChoice,
    NodeConfig,
    NodeEnvelope,
    QueuePeekEntry,
    QueuePeekResponse,
    RouteClaimResponse,
    RouteView,
    RunnerFactAck,
    RunnerView,
)

#: The runner-fact kind that advances the mock's fence (``blizzard.wire.facts.LEASE_MINTED``).
LEASE_MINTED = "lease.minted"


class ChunkNotFound(Exception):
    """No seeded chunk with that id."""


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
            entry=spec.entry,
            nodes=spec.nodes,
            pm_pointers=spec.pm_pointers,
        )
        self._state.put_chunk(chunk)
        return chunk

    def reset(self) -> None:
        self._state.clear()
        self._levers.clear_all()

    # -- queue -------------------------------------------------------------

    def peek(self) -> QueuePeekResponse:
        """Ready = seeded, unclaimed, not terminal — FIFO by insertion (D-080)."""
        ready = [c for c in self._state.list_chunks() if not c.claimed and c.status is ChunkStatus.READY]
        entries = [
            QueuePeekEntry(
                chunk_id=c.chunk_id,
                graph_id=c.graph_id,
                position=i,
                pm_pointers=[p.model_dump() for p in c.pm_pointers],
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
        )

    # -- reads -------------------------------------------------------------

    def chunk_detail(self, chunk_id: str) -> ChunkDetail:
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
        return ChunkDetail(
            chunk_id=chunk.chunk_id,
            graph_id=chunk.graph_id,
            status=chunk.status.value,
            current_node_id=chunk.current_node_id,
            latest_epoch=chunk.latest_epoch or None,
            pm_pointers=[p.model_dump() for p in chunk.pm_pointers],
            route=route,
        )

    def envelope(self, chunk_id: str) -> NodeEnvelope:
        """The current node's envelope (idempotent re-read, D-090).

        The ``stale_envelope`` lever stamps ``latest_epoch - 1`` so a completion built
        from it is fenced out as a zombie — the runner's stale-envelope path (D-007)."""
        chunk = self._require(chunk_id)
        node_id = chunk.current_node_id or chunk.entry
        epoch = chunk.latest_epoch
        stale = self._levers.find(HubLever.STALE_ENVELOPE.value, chunk_id)
        if stale is not None:
            self._levers.consume(stale)
            epoch = max(chunk.latest_epoch - 1, 0)
        return self._envelope(chunk, node_id, epoch=epoch)

    # -- fact intake (fence) ----------------------------------------------

    def ingest_facts(self, runner_id: str, facts: list[dict[str, Any]]) -> RunnerFactAck:
        """Apply a batched ``POST /events`` push; ``lease.minted`` advances the fence (D-044)."""
        applied: list[int] = []
        high_water = 0
        for fact in facts:
            seq = int(fact.get("seq", 0))
            high_water = max(high_water, seq)
            kind = str(fact.get("kind", ""))
            payload = fact.get("payload") or {}
            if kind == LEASE_MINTED and isinstance(payload, dict):
                chunk_id = str(payload.get("chunk_id", ""))
                epoch = int(payload.get("epoch", 0))
                chunk = self._state.get_chunk(chunk_id)
                if chunk is not None:
                    chunk.latest_epoch = max(chunk.latest_epoch, epoch)
                    self._state.put_chunk(chunk)
            applied.append(seq)
        return RunnerFactAck(runner_id=runner_id, high_water=high_water, applied=applied)

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

    # -- registry ----------------------------------------------------------

    def register(self, runner_id: str, workspace_id: str) -> bool:
        return self._state.upsert_runner(runner_id, workspace_id, self._clock.now())

    def runner_view(self, runner_id: str) -> RunnerView | None:
        row = self._state.get_runner(runner_id)
        if row is None:
            return None
        return RunnerView(
            runner_id=row.runner_id,
            workspace_id=row.workspace_id,
            registered_at=row.registered_at.isoformat(),
            last_seen_at=row.last_seen_at.isoformat(),
            online=True,
            paused=row.paused,
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
            # A hub (deliver) node takes over; the mock delivers instantly and the chunk
            # derives done — the runner holds envs, polls get_chunk, sees done, releases.
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
                judged_by=node.judged_by,
                checks=node.checks,
                produces=node.produces,
                retries_max=node.retries_max,
                mode=node.mode,
                choices=[EnvelopeChoice(name=c.name, description=c.description) for c in node.choices],
            ),
            prompt=node.prompt,
            judgement_prompt=node.judgement_prompt,
            pm_pointers=[p.model_dump() for p in chunk.pm_pointers],
        )

    def _require(self, chunk_id: str) -> ChunkState:
        chunk = self._state.get_chunk(chunk_id)
        if chunk is None:
            raise ChunkNotFound(f"unknown chunk {chunk_id}")
        return chunk
