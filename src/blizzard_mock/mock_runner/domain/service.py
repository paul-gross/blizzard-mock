"""``MockRunnerService`` — the mock runner's driving rules over a hub gateway and levers.

The domain layer (``bzh:domain-core``): it performs the runner's outbound protocol against
a hub — register, peek, claim (recording the lease + reporting ``lease.minted``), and
complete (an epoch-fenced submission) — exactly as the real runner does, but under lever
control. Each lever distorts one outbound call so the hub-under-test meets the named
misbehaviour over the wire. Collaborators are injected at the composition root
(``bzh:dependency-injection``).
"""

from __future__ import annotations

import time
from typing import Any

from blizzard_mock.clock import Clock
from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_runner.domain.gateway import IHubGateway
from blizzard_mock.mock_runner.domain.levers import RunnerLever
from blizzard_mock.mock_runner.domain.models import Held

#: The runner-fact kind that advances the hub's fence (``blizzard.wire.facts.LEASE_MINTED``).
LEASE_MINTED = "lease.minted"


class MockRunnerService:
    """The composition-root-wired driver every mock-runner control route delegates to."""

    def __init__(
        self, gateway: IHubGateway, levers: ILeverStore, clock: Clock, *, runner_id: str, workspace_id: str
    ) -> None:
        self._gw = gateway
        self._levers = levers
        self._clock = clock
        self._runner_id = runner_id
        self._workspace_id = workspace_id
        self._held: dict[str, Held] = {}

    @property
    def levers(self) -> ILeverStore:
        return self._levers

    @property
    def runner_id(self) -> str:
        return self._runner_id

    def reset(self) -> None:
        self._held.clear()
        self._levers.clear_all()

    # -- drive verbs -------------------------------------------------------

    def register(self) -> dict[str, Any]:
        self._apply_delay(None)
        status, body = self._gw.register(self._runner_id, self._workspace_id)
        return {"status": status, "response": body}

    def peek(self) -> dict[str, Any]:
        self._apply_delay(None)
        status, body = self._gw.peek()
        return {"status": status, "response": body}

    def claim(self, chunk_id: str, environment_ids: list[str]) -> dict[str, Any]:
        """Claim a chunk and record the held lease; report ``lease.minted`` (D-044)."""
        self._apply_delay(chunk_id)
        status, body = self._gw.claim(
            {
                "chunk_id": chunk_id,
                "runner_id": self._runner_id,
                "workspace_id": self._workspace_id,
                "environment_ids": environment_ids,
            }
        )
        if status != 201:
            return {"claimed": False, "status": status, "response": body}
        envelope = body.get("envelope", {})
        node_id = envelope.get("node", {}).get("node_id", "")
        held_epoch = int(envelope.get("epoch", 0)) + 1  # the real runner mints latest+1
        self._held[chunk_id] = Held(chunk_id=chunk_id, epoch=held_epoch, from_node_id=node_id)
        self._report_lease(chunk_id, held_epoch)
        return {"claimed": True, "status": status, "from_node_id": node_id, "epoch": held_epoch, "response": body}

    def complete(self, chunk_id: str, choice: str) -> dict[str, Any]:
        """Submit the held node-step's completion, distorted by any armed lever."""
        self._apply_delay(chunk_id)
        held = self._held.get(chunk_id)
        if held is None:
            return {"drove": False, "reason": f"chunk {chunk_id} not claimed by this driver"}

        if self._pull(RunnerLever.UNREACHABLE, chunk_id):
            # Vanish mid-lease: never call the hub, leaving a claimed-but-unfinished chunk.
            return {"drove": False, "reason": "runner unreachable mid-lease"}

        epoch = held.epoch
        if self._pull(RunnerLever.STALE_EPOCH, chunk_id):
            epoch = max(held.epoch - 1, 0)  # a zombie fence the hub rejects (D-007)
        from_node = held.from_node_id
        if self._pull(RunnerLever.CONFLICTING_FACT, chunk_id):
            from_node = "conflicting-node"  # a fact that does not match the hub's current node

        submission = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": self._runner_id,
            "from_node_id": from_node,
            "check_results": [],
            "artifacts": [],
        }
        held.last_submission = submission
        status, body = self._gw.submit_completion(chunk_id, submission)

        replayed: dict[str, Any] | None = None
        if self._pull(RunnerLever.REPLAY, chunk_id):
            rstatus, rbody = self._gw.submit_completion(chunk_id, submission)  # duplicate delivery
            replayed = {"status": rstatus, "response": rbody}

        if self._pull(RunnerLever.DROP_ACK, chunk_id):
            # Lose the ack: report over the wire but do not advance the held lease.
            return {"drove": True, "dropped_ack": True, "status": status, "response": body, "replayed": replayed}

        if status == 200:
            self._advance(chunk_id, body)
        return {"drove": True, "status": status, "response": body, "replayed": replayed}

    def get_chunk(self, chunk_id: str) -> dict[str, Any]:
        self._apply_delay(chunk_id)
        status, body = self._gw.get_chunk(chunk_id)
        return {"status": status, "response": body}

    def held(self, chunk_id: str) -> Held | None:
        return self._held.get(chunk_id)

    # -- internals ---------------------------------------------------------

    def _advance(self, chunk_id: str, body: dict[str, Any]) -> None:
        """On a NEXT apply-response, move the held lease to the next node and re-fence."""
        held = self._held.get(chunk_id)
        if held is None:
            return
        if body.get("outcome") == "next" and body.get("next_envelope"):
            nxt = body["next_envelope"]
            held.from_node_id = nxt.get("node", {}).get("node_id", held.from_node_id)
            held.epoch = int(nxt.get("epoch", 0)) + 1
            self._report_lease(chunk_id, held.epoch)
        else:
            self._held.pop(chunk_id, None)  # hub-node / done / failure — the tenure is over

    def _report_lease(self, chunk_id: str, epoch: int) -> None:
        held = self._held.get(chunk_id)
        seq = (held.seq + 1) if held is not None else 1
        if held is not None:
            held.seq = seq
        self._gw.report_lease(
            chunk_id,
            {
                "runner_id": self._runner_id,
                "facts": [{"seq": seq, "kind": LEASE_MINTED, "payload": {"chunk_id": chunk_id, "epoch": epoch}}],
            },
        )

    def _apply_delay(self, chunk_id: str | None) -> None:
        lever = self._levers.find(RunnerLever.DELAY.value, chunk_id)
        if lever is not None:
            self._levers.consume(lever)
            time.sleep(int(lever.payload.get("ms", 0)) / 1000.0)

    def _pull(self, kind: RunnerLever, chunk_id: str) -> bool:
        lever = self._levers.find(kind.value, chunk_id)
        if lever is None:
            return False
        self._levers.consume(lever)
        return True
