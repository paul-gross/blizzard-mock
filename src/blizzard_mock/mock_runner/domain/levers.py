"""The mock runner's lever vocabulary — the runner-side distortions a hub must survive.

Where the mock hub's levers bend the *responses* a runner receives, the mock runner is a
**driver**: it performs the runner's outbound protocol (peek → claim → complete) against a
real (or mock) hub, and its levers distort those *outbound calls*. Building the **hub**
against this mock (``implementation/mocking.md``, "the hub → run it against the mock
runner") means arming a lever that names the misbehaviour the hub must reject or absorb —
a stale-epoch completion, a duplicate delivery, a runner that vanishes mid-lease.

Six of the eight levers mirror the shared menu, realised on the runner's side of the
wire; ``stale_route_token``/``omit_route_token`` (issue #84b) are runner-only — driving
the route capability token's presentation is a mock-runner lever, not a hub distortion.
"""

from __future__ import annotations

from enum import StrEnum


class RunnerLever(StrEnum):
    """The named misbehaviours the mock runner can be steered into."""

    #: Sleep ``payload.ms`` before the next outbound call (delay a response).
    DELAY = "delay"
    #: Submit the completion, then discard the hub's ack — the runner "loses" the reply and
    #: does not advance its held lease (drop a completion ack).
    DROP_ACK = "drop_ack"
    #: Submit a completion naming the *wrong* ``from_node_id`` — a conflicting fact the hub
    #: must reject rather than mis-apply.
    CONFLICTING_FACT = "conflicting_fact"
    #: Claim, then never complete — the runner goes unreachable *mid-lease*, leaving the hub
    #: a claimed-but-unfinished chunk to reap.
    UNREACHABLE = "unreachable"
    #: Submit the same completion twice (a duplicate delivery) — the hub must apply it once
    #: (epoch-idempotent, D-090) and reject/absorb the replay.
    REPLAY = "replay"
    #: Submit a completion carrying a **stale** (held-epoch minus 1) fence — the zombie the
    #: hub fences out over the wire (D-007).
    STALE_EPOCH = "stale_epoch"
    #: Submit the completion carrying a **wrong** route capability token (issue #84b) —
    #: neither the held claim's own token nor any token the hub minted for this chunk —
    #: so a service test can drive the hub's route-token check without a real runner.
    STALE_ROUTE_TOKEN = "stale_route_token"
    #: Submit the completion carrying **no** route capability token at all (issue #84b) —
    #: the pre-#84a-runner / dropped-field case ``route_token_mode=warn`` must absorb and
    #: ``enforce`` must reject.
    OMIT_ROUTE_TOKEN = "omit_route_token"


CATALOG: dict[str, str] = {
    RunnerLever.DELAY.value: "sleep payload.ms before the next outbound call",
    RunnerLever.DROP_ACK.value: "submit the completion but discard the hub's ack (do not advance)",
    RunnerLever.CONFLICTING_FACT.value: "submit a completion naming the wrong from_node_id",
    RunnerLever.UNREACHABLE.value: "claim then never complete — vanish mid-lease",
    RunnerLever.REPLAY.value: "submit the same completion twice (duplicate delivery)",
    RunnerLever.STALE_EPOCH.value: "submit a completion with a stale (held-epoch - 1) fence (D-007)",
    RunnerLever.STALE_ROUTE_TOKEN.value: "submit a completion with a wrong route capability token (issue #84b)",
    RunnerLever.OMIT_ROUTE_TOKEN.value: "submit a completion with no route capability token (issue #84b)",
}
