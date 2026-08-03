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
import uuid
from typing import Any

from blizzard_mock.clock import Clock
from blizzard_mock.levers import ILeverStore
from blizzard_mock.mock_runner.domain.gateway import IHubGateway
from blizzard_mock.mock_runner.domain.levers import RunnerLever
from blizzard_mock.mock_runner.domain.models import Held

#: The runner-fact kind that advances the hub's fence (``blizzard.wire.facts.LEASE_MINTED``).
LEASE_MINTED = "lease.minted"
#: The remaining fact kinds this driver can push over ``/events``
#: (``blizzard.wire.facts``) — not fence-advancing, so ``ingest_facts`` need not
#: understand them for the fact push itself to be observable over the wire.
QUESTION_ASKED = "question.asked"
RUNNER_LOCALLY_PAUSED = "runner.locally_paused"
RUNNER_LOCALLY_RESUMED = "runner.locally_resumed"
EVENT_RECORDED = "event.recorded"


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
        #: Monotonic sequence for facts that are not chunk-scoped leases (question/pause/
        #: resume) — independent of a ``Held``'s own per-chunk lease-fact counter.
        self._runner_seq = 0
        #: The mock's own local git-commit declaration store (issue #143, Phase 3) —
        #: ``lease_id -> repo -> {forge, repo, branch, commit}``, latest-wins per
        #: ``(lease_id, repo)``, mirroring the real runner's ``git_commit_declarations``
        #: table. Purely local: no hub call backs it, exactly as the real declare channel
        #: makes no hub call this phase.
        self._git_commit_declarations: dict[str, dict[str, dict[str, str]]] = {}

    @property
    def levers(self) -> ILeverStore:
        return self._levers

    @property
    def runner_id(self) -> str:
        return self._runner_id

    def reset(self) -> None:
        self._held.clear()
        self._runner_seq = 0
        self._git_commit_declarations.clear()
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
        route_token = body.get("route_token")
        self._held[chunk_id] = Held(chunk_id=chunk_id, epoch=held_epoch, from_node_id=node_id, route_token=route_token)
        self._report_lease(chunk_id, held_epoch)
        return {"claimed": True, "status": status, "from_node_id": node_id, "epoch": held_epoch, "response": body}

    def complete(
        self,
        chunk_id: str,
        choice: str,
        artifacts: list[dict[str, Any]] | None = None,
        check_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Submit the held node-step's completion, distorted by any armed lever.

        ``artifacts`` (the submission's ``produces:`` artifacts, ``SubmittedArtifact``
        dicts) default empty — the historical behaviour — so only a produces-aware
        service test passes them, to drive the hub's ``produces_mode`` backstop.
        ``check_results`` (the runner-executed check facts, ``CheckResult`` dicts, issue
        #114) default empty likewise — a checks-gate service test sets them to drive the
        hub's ``requires_checks`` backstop over the wire."""
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

        # Route capability token (issue #84b): stamp the held claim's own plaintext by
        # default (mirroring the real runner's stash-and-stamp), unless a lever overrides
        # it for this one call — a wrong token, or none at all.
        route_token = held.route_token
        if self._pull(RunnerLever.OMIT_ROUTE_TOKEN, chunk_id):
            route_token = None
        if self._pull(RunnerLever.STALE_ROUTE_TOKEN, chunk_id):
            route_token = "mock-stale-route-token-does-not-match-any-live-route"

        submission: dict[str, Any] = {
            "choice": choice,
            "epoch": epoch,
            "runner_id": self._runner_id,
            "from_node_id": from_node,
            "check_results": check_results or [],
            "artifacts": artifacts or [],
        }
        if route_token is not None:
            submission["route_token"] = route_token
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

    def declare_git_commit(self, lease_id: str, *, forge: str, repo: str, branch: str, commit: str) -> dict[str, Any]:
        """Record a worker's explicit git-commit declaration for ``repo`` against
        ``lease_id`` (issue #143, Phase 3) — the mock's own structural sibling of the real
        runner's ``GitCommitDeclarationService.declare``. Append-and-read-newest per
        ``(lease_id, repo)``, no hub call: the served route and the ``/_drive/*`` lever
        both land here, exactly as the real store backs both the CLI-driven route and
        (in the real runner) nothing else this phase."""
        self._git_commit_declarations.setdefault(lease_id, {})[repo] = {
            "forge": forge,
            "repo": repo,
            "branch": branch,
            "commit": commit,
        }
        return {"recorded": True, "lease_id": lease_id, "repo": repo}

    def git_commits_for_lease(self, lease_id: str) -> dict[str, dict[str, str]]:
        """The lease's declared git commits, newest per repo — the read-back a service
        test drives via ``/_drive/get-git-commits`` (issue #143, Phase 3)."""
        return dict(self._git_commit_declarations.get(lease_id, {}))

    def escalate(self, chunk_id: str, takeover_command: str = "", wrapped_takeover_command: str = "") -> dict[str, Any]:
        """Report retries-exhausted via the dedicated route, fenced by the held epoch."""
        self._apply_delay(chunk_id)
        held = self._held.get(chunk_id)
        if held is None:
            return {"drove": False, "reason": f"chunk {chunk_id} not claimed by this driver"}
        body = {
            "epoch": held.epoch,
            "runner_id": self._runner_id,
            "takeover_command": takeover_command,
            "wrapped_takeover_command": wrapped_takeover_command,
        }
        status, response = self._gw.report_escalation(chunk_id, body)
        return {"drove": True, "status": status, "response": response}

    def decide(self, chunk_id: str, choice: str | None = None) -> dict[str, Any]:
        """Submit a decision at the held node — a runner-config gate parks the chunk.

        ``choice`` is accepted for drive-body symmetry but is not part of the wire
        submission (see ``DecideBody``) — unused here, cosmetic."""
        self._apply_delay(chunk_id)
        held = self._held.get(chunk_id)
        if held is None:
            return {"drove": False, "reason": f"chunk {chunk_id} not claimed by this driver"}
        body: dict[str, Any] = {
            "from_node_id": held.from_node_id,
            "epoch": held.epoch,
            "runner_id": self._runner_id,
            "artifacts": [],
        }
        if held.route_token is not None:
            body["route_token"] = held.route_token
        status, response = self._gw.submit_decision(chunk_id, body)
        return {"drove": True, "status": status, "response": response}

    def ask(self, chunk_id: str, question: str, options: list[str] | None = None) -> dict[str, Any]:
        """Push a ``question.asked`` fact via ``/events`` — mints a pollable question
        hub-side. Returns the minted ``question_id`` so a test can poll it."""
        self._apply_delay(chunk_id)
        held = self._held.get(chunk_id)
        if held is None:
            return {"drove": False, "reason": f"chunk {chunk_id} not claimed by this driver"}
        question_id = f"mock-question-{uuid.uuid4().hex[:24]}"
        self._runner_seq += 1
        payload = {
            "question_id": question_id,
            "chunk_id": chunk_id,
            "runner_id": self._runner_id,
            "epoch": held.epoch,
            "question": question,
            "options": options or [],
            "asked_at": self._clock.now().isoformat(),
        }
        status, response = self._gw.push_facts(
            {
                "runner_id": self._runner_id,
                "facts": [{"seq": self._runner_seq, "kind": QUESTION_ASKED, "payload": payload}],
            }
        )
        return {"drove": True, "question_id": question_id, "status": status, "response": response}

    def poll_answer(self, question_id: str) -> dict[str, Any]:
        """``GET /questions/{id}`` — the runner's answer poll."""
        self._apply_delay(None)
        status, response = self._gw.get_question(question_id)
        return {"status": status, "response": response}

    def pause(self, by: str = "operator", reason: str | None = None) -> dict[str, Any]:
        """Push a runner-scoped ``runner.locally_paused`` fact via ``/events`` (no
        ``chunk_id``)."""
        self._apply_delay(None)
        self._runner_seq += 1
        payload: dict[str, Any] = {"at": self._clock.now().isoformat(), "by": by}
        if reason is not None:
            payload["reason"] = reason
        status, response = self._gw.push_facts(
            {
                "runner_id": self._runner_id,
                "facts": [{"seq": self._runner_seq, "kind": RUNNER_LOCALLY_PAUSED, "payload": payload}],
            }
        )
        return {"status": status, "response": response}

    def resume(self, by: str = "operator") -> dict[str, Any]:
        """Push a runner-scoped ``runner.locally_resumed`` fact via ``/events`` (no
        ``chunk_id``)."""
        self._apply_delay(None)
        self._runner_seq += 1
        payload: dict[str, Any] = {"at": self._clock.now().isoformat(), "by": by}
        status, response = self._gw.push_facts(
            {
                "runner_id": self._runner_id,
                "facts": [{"seq": self._runner_seq, "kind": RUNNER_LOCALLY_RESUMED, "payload": payload}],
            }
        )
        return {"status": status, "response": response}

    def report_event(
        self,
        *,
        severity: str,
        kind: str,
        message: str,
        chunk_id: str | None = None,
        lease_id: str | None = None,
        node_name: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Push one ``event.recorded`` operational-event fact via ``/events`` (issue #125).

        The wire counterpart to the real runner's failure-event emission (its Phase 3
        `_fail_attempt`/per-adapter sites): the hub folds this into its append-only
        ``event_log`` and re-broadcasts it as ``event-logged``. ``chunk_id`` is optional —
        a runner-scoped event names none, exactly like ``pause``/``resume``. Not
        fence-advancing, so no held lease is required."""
        self._apply_delay(chunk_id)
        self._runner_seq += 1
        payload: dict[str, Any] = {"severity": severity, "kind": kind, "message": message}
        if chunk_id is not None:
            payload["chunk_id"] = chunk_id
        if lease_id is not None:
            payload["lease_id"] = lease_id
        if node_name is not None:
            payload["node_name"] = node_name
        if detail is not None:
            payload["detail"] = detail
        status, response = self._gw.push_facts(
            {
                "runner_id": self._runner_id,
                "facts": [{"seq": self._runner_seq, "kind": EVENT_RECORDED, "payload": payload}],
            }
        )
        return {"drove": True, "status": status, "response": response}

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
        """Advance the hub's fence for the held chunk.

        Not lever-distorted for correctness — the report itself is always genuine,
        because a completion test's setup depends on the fence landing regardless of
        which drive-level lever is armed (see ``complete``'s docstring history). Only
        the *transport path* is lever-selectable: by default this hits the dedicated
        ``/chunks/{id}/leases`` route, mirroring the real runner; ``lease_via_events``
        routes the identical report through the batched ``/events`` fact-push instead —
        the mock's original path, retained so a test can still exercise it.
        """
        held = self._held.get(chunk_id)
        if self._pull(RunnerLever.LEASE_VIA_EVENTS, chunk_id):
            seq = (held.seq + 1) if held is not None else 1
            if held is not None:
                held.seq = seq
            payload: dict[str, Any] = {"chunk_id": chunk_id, "epoch": epoch}
            # Stamp the held claim's own route token (issue #84b) — always, not
            # lever-controlled: this is the genuine fence-advancing report a completion
            # test's own setup depends on, so it must keep landing under
            # ``route_token_mode=enforce`` exactly as the real runner's does. The
            # route-token drive levers (above) distort only the driven ``/_drive/complete``
            # call, the surface a service test actually exercises.
            if held is not None and held.route_token is not None:
                payload["route_token"] = held.route_token
            self._gw.report_lease_via_events(
                chunk_id,
                {"runner_id": self._runner_id, "facts": [{"seq": seq, "kind": LEASE_MINTED, "payload": payload}]},
            )
            return
        body: dict[str, Any] = {"epoch": epoch, "runner_id": self._runner_id}
        self._gw.report_lease_direct(chunk_id, body)

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
